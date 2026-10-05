# ResolveFlow AI - Client Track Handoff

**Current phase:** C12 complete

**Gate:** GREEN for C12. The C02 definition gate is also green; *applying* that infrastructure remains blocked on client inputs and explicit authorization.

**Last updated:** 5 October 2026

## Completed

- Audited all repository Markdown, application modules, tests, scripts, package/container configuration, sample data, tracked files, ignored local data, and plan documents.
- Verified the current MVP architecture and the absence of production Next.js, authentication, organization/RBAC, Gmail, durable workers, hosted PostgreSQL/pgvector, CI, and infrastructure as code.
- Ran the complete offline automated suite: 43 passed.
- Ran application import, Make target, and Docker Compose service-resolution checks.
- Confirmed the local `.env` is ignored and untracked without exposing values.
- Scanned current tracked source and commit history for bounded key-shaped patterns; no non-placeholder match was found.
- Recorded dedicated-client scope, ownership, unknown client inputs, production decisions, gap evidence, risks, and phase status.
- Performed no deployment, external mutation, mailbox connection, paid-model call, or deliberate knowledge-content change. Offline tests exercised ignored local test/retrieval state.
- Added the Next.js/TypeScript production web foundation and kept Streamlit available as an internal reference.
- Added a private FastAPI `v1` boundary over the existing Python triage/RAG/MCP behavior.
- Added generated OpenAPI, strict request/response schemas, Python/web contract tests, and an explicit no-send contract.
- Added pinned Node/npm and Python development workflows, Ruff, mypy, ESLint, TypeScript, and root verification commands.
- Added CI for contract drift, tests, lint, types, production build, and both container builds.
- Built both production-shape containers and verified the local two-service health path.
- Ran the final C01 gate: 50 Python tests, PostgreSQL migration rendering, Ruff, mypy, ESLint, TypeScript, one web contract test, and the Next.js production build all passed.
- Recorded full C01 evidence in `CLIENT_C01_TEST_REPORT.md`.
- Defined Terraform for two fully separate client-owned environments from 13 single-purpose modules, plus a bootstrap root module for the state bucket.
- Covered IAM, Artifact Registry, Cloud Run, Cloud SQL/pgvector, Storage, Pub/Sub with dead letters, Cloud Tasks, Cloud Scheduler, Secret Manager, KMS, VPC with private service access and Cloud NAT, observability, backups with PITR, and budgets.
- Made the AI API and ingestion worker internal-only, left the web tier as the single public surface, and gave Cloud SQL no public IP and no database password (IAM authentication).
- Kept every secret value out of Terraform: the module creates empty containers and per-secret grants only.
- Set up keyless CI through Workload Identity Federation with deploy privilege separated from Terraform privilege.
- Added `scripts/validate_infra.py`, an offline HCL wiring and posture checker, and 24 tests including a negative test for every posture check.
- Verified offline: `terraform fmt` clean, `terraform validate` passes for all three root modules against google provider 6.50.0, committed multi-platform provider lock files, and the full suite at 74 passed.
- Recorded full C02 evidence in `CLIENT_C02_TEST_REPORT.md`.
- Added the identity schema: organizations, approved domains, users, sessions, departments, memberships, invitations, and append-only audit events, every tenant table scoped by `organization_id`.
- Implemented Auth.js Google Workspace sign-in with database sessions, an approved-domain allowlist, and a required invited membership.
- Built a pure authorization core: six roles, 25 permissions, one decision function with stable reason codes and deny-by-default.
- Made identity re-derive from the database on every protected request, so revoking a membership takes effect immediately.
- Added server-derived actor context to the internal Python boundary, retiring the C01 placeholder recorded in C-D018.
- Added a one-time, refusing bootstrap for the single organization and its Owner.
- Verified against live PostgreSQL: migration applies, downgrades cleanly, re-upgrades, and every constraint rejects what it should.
- Recorded full C03 evidence in `CLIENT_C03_TEST_REPORT.md`.
- Added the durable operational model in two migrations: queues, mailboxes and permissions, threads, messages, attachments, tickets, assignments, per-user seen state, and SLA.
- Added knowledge sources/articles/chunks/embeddings with pgvector HNSW plus a generated full-text index, preserving the proven hybrid retrieval shape.
- Added AI configs that can hold only a Secret Manager reference, triage runs that cannot store an ungrounded auto-resolve draft, attributable actions, idempotent jobs, usage, and versioned evaluations.
- Added retention policies, legal holds, an append-only sweep log, and `app/retention.py` with pure planning and a hold-aware executor that defaults to a dry run.
- Verified live on PostgreSQL 16 with pgvector 0.8.6: applied, downgraded to base with no orphaned types or functions, re-upgraded, every constraint rejected what it should, retention and legal hold behaved, and a dump/restore round trip was faithful with all triggers and constraints still enforcing.
- Recorded full C04 evidence in `CLIENT_C04_TEST_REPORT.md`.
- Added production knowledge ingestion for PDF, Markdown, text, HTML, and CSV with one deterministic normalisation pass and visible per-source failures.
- Added passage chunking with exact character offsets and an enforced invariant that every chunk slices back to the stored body, so a citation names real text.
- Added pgvector semantic plus PostgreSQL full-text retrieval, scoped by organization, publication status, and deletion in SQL rather than after the fact.
- Preserved the proven rerank formula component for component, importing the MVP's tokeniser rather than reimplementing it.
- Added curated per-article search terms, restoring a signal the first port dropped.
- Added a labelled evaluation harness with versioned thresholds and an explicit old-versus-new comparison.
- Measured: Recall@1, Recall@3, and MRR all 1.000 on both the MVP and pgvector paths, with zero regression, using deterministic embeddings and no paid model call.
- Verified live that archived, soft-deleted, and other-organization content cannot be retrieved, and that every citation slices back to its stored passage.
- Recorded full C05 evidence in `CLIENT_C05_TEST_REPORT.md`.
- Added read-only Gmail connection: exactly `gmail.readonly`, `openid`, and `userinfo.email`, with granted scopes checked for both missing and excess entries.
- Sealed refresh tokens with AES-256-GCM bound to organization, mailbox, and purpose, with key ids for rotation; access tokens are never stored and OAuth state is stored only as a hash.
- Verified the account Google returns against the mailbox being connected, and revoked at Google every token that fails policy rather than discarding it.
- Made connection attempts single-use and bound to their initiator, checking ownership before consumption and replay before expiry.
- Classified refresh failures: a revoked grant withdraws the mailbox, a transient failure degrades it, and an unreadable credential never reaches Google.
- Added internal API endpoints and web routes for start, callback, and revoke, guarded against open redirects, reflected input, and cross-origin requests.
- Documented connection modes - shared mailboxes, individual mailboxes, Groups, aliases, and domain-wide delegation - for the client's Workspace administrator.
- Proved every gate case offline and end to end through the real service and PostgreSQL store, and required each of 16 database constraints to reject for its own named reason.
- Recorded full C06 evidence in `CLIENT_C06_TEST_REPORT.md`.
- Added Gmail ingestion: watches, push-notification vetting, history cursors, reconciliation, normalization, and recovery, writing into the C04 tables with no new migration.
- Made a notification a hint rather than an instruction: every sync reads from the mailbox's stored cursor, so duplicated, delayed, and out-of-order notifications converge on the same result.
- Advanced the cursor only after a window's work succeeded, and only forwards, with monotonicity enforced in SQL.
- Made deduplication structural: the Pub/Sub message id is the job key, messages and threads rely on the C04 per-mailbox unique indexes, and thread counters are recomputed rather than incremented.
- Classified every Gmail failure to its one correct recovery: stale cursor reconciles, quota defers with bounded backoff, revoked grant stops, deleted message is skipped.
- Added job leases with reclaim so a crashed worker's jobs return to the queue, and dead-lettering at the attempt limit.
- Treated email as untrusted input: Gmail's receipt time over the forgeable Date header, CR/LF stripped from headers, filenames reduced to a basename, bodies and recipient lists bounded, attachments classified but never downloaded.
- Cached access tokens in memory for 45 minutes and dropped them the moment Gmail rejected one.
- Proved all seven gate scenarios offline and end to end on PostgreSQL, simulated fifty mailboxes with a full replay adding nothing, and required each constraint to reject on its named reason.
- Recorded full C07 evidence in `CLIENT_C07_TEST_REPORT.md`.
- Built the operations workspace: overview, unified inbox with seven saved views and URL-backed filters, and the ticket workspace with conversation, context, decision, draft, history, and trace.
- Decided visibility once, in SQL: organization, mailbox permission, and department are a single predicate on every workspace query, mirroring the authorization core.
- Derived per-person unseen state by comparing read position against the thread's latest message, storing no unread flag and never reading Gmail's shared label.
- Made colour a checked contract: every text colour is a foreground/background token pair, and a test applies the WCAG formula on every commit.
- Kept JavaScript to an accelerator: filters are a plain GET form, assignment is a server action, and keyboard shortcuts stand aside while someone is typing.
- Verified in a real browser as two different people that mailbox scoping, per-person read state, 404-on-no-access, role-dependent controls, keyboard order, contrast, and the phone layout all behave.
- Recorded full C08 evidence in `CLIENT_C08_TEST_REPORT.md`.
- Built the rule engine in a fixed order: the MVP guardrails, imported unchanged, then versioned client routing, then service levels.
- Made the safety property structural: a client rule can send a conversation to a person but never to the model, and safety codes survive routing.
- Pinned the final route to the graph's exact precedence with 224 cases compared against `app.graph.decide`.
- Defined rules as data in a closed language, versioned with effective windows, first-wins per field, with equal-priority disagreement reported as a conflict.
- Made unresolvable configuration fail toward a person: rule conflicts, unreadable stored rules, forced escalation, and SLA policy ties.
- Added VIP matching (address before domain, no suffix matching), organization and department holidays, and specificity-ranked SLA policies.
- Made working-time arithmetic correct across both daylight-saving transitions by doing every calculation in UTC, after finding that same-zone Python datetimes are compared by wall clock.
- Caught 14 of 14 injected defects, one only after two boundary tests were added, and passed 49 of 49 live PostgreSQL checks.
- Recorded full C09 evidence in `CLIENT_C09_TEST_REPORT.md`.
- Put every model call behind one gateway that refuses first: unapproved model, wrong region, no budget, no headroom, or unreadable key, all before a request is built.
- Reserved each call's worst-case cost against the monthly budget in the statement that checks it, so concurrent calls cannot overspend, and kept no transaction open while a provider is called.
- Read the client's key from Secret Manager at call time only, behind a value that refuses to print itself, and proved a replacement key against the provider before storing it.
- Reduced every provider failure to a stable code with a safe message, after finding three exception chains that carried a key, a bearer token, and raw secret bytes.
- Allowed fallback only for availability failures, only to approved models in the same region, never for embeddings, and recorded every rejected candidate.
- Gave operators a masked settings page: status, budget meter, recent problems in plain words, fallback use, and check or replace for Owners and Administrators.
- Ran the MVP's proven prompts unchanged through the gateway, so a provider failure reaches a person as MODEL_ERROR.
- Caught 29 of 29 injected defects, passed 67 of 67 live PostgreSQL checks, and verified the page in a browser as three different people.
- Called no paid model and sent no request to OpenAI.
- Recorded full C10 evidence in `CLIENT_C10_TEST_REPORT.md`.
- Ran the proven MVP workflow on the production schema, filling its two seams with the client's own knowledge over MCP and the C10 gateway, and reimplementing none of it.
- Decided the route twice from the same evidence - once by the workflow, once by C09's rules - and kept the more cautious answer, withholding any draft the rules overruled.
- Kept the knowledge boundary a real process boundary, with one reused server, a deadline on every search, and a failed session replaced rather than handed on.
- Recorded each run, its action, the routing it applied and its service-level targets in one transaction, keyed by a correlation id that also labels its provider calls, so cost joins back to the run.
- Made every expected failure end in a stored run routed to a person, because a pipeline that throws leaves no record of why nothing happened.
- Added Quality Check: six gates against versioned thresholds on a labelled dataset, each stored with the corpus fingerprint it measured, and each deliberately broken in the tests.
- Added a demonstration that shows the whole chain with real values - request, model call, guardrails, MCP call, knowledge response, decision, grounded answer, validator, record.
- Gave operators a read-only Quality page that says what failed, in what units, and whether the knowledge has changed since the numbers were measured.
- Caught 20 of 20 injected defects, passed 26 of 26 live PostgreSQL checks, and verified the page and the populated ticket trace in a browser as three different people.
- Called no paid model and sent nothing to any customer.
- Recorded full C11 evidence in `CLIENT_C11_TEST_REPORT.md`.
- Put every human decision - edit, approve, reject, reroute, assign, resolve - through one service, in one order, with one set of rules about who may decide what.
- Made every decision name the ticket version it was made against, so two reviewers acting at once produce one change and one visible conflict rather than two silent ones.
- Recorded refusals as well as decisions: a wrong role, a missing reason or a stale view is written down with its code and audited.
- Kept the model's answer as revision 1, never overwritten; a human edit is a new revision with its author, and approving your own words is recorded as yours.
- Built and tested the Gmail draft path, and then - on the client's instruction - made it possible: consent can now ask for `gmail.compose`, and the database accepts a compose-scoped credential while still refusing every scope that could send. Drafting is off by default and takes a deliberate deployment change plus reconnecting the mailbox.
- Showed the reviewer exactly what approving would create - recipient, subject, body, thread - and said plainly, every time, that ResolveFlow never sends.
- Claimed each provider draft before calling the provider, with one live draft per conversation, so a retry cannot leave two drafts in the client's mailbox.
- Caught 24 of 24 injected defects, passed 62 of 62 live PostgreSQL checks, and found five real defects by clicking the controls in a browser.
- Sent nothing, called no paid model, and connected no mailbox.
- Recorded full C12 evidence in `CLIENT_C12_TEST_REPORT.md`, with the compose scope change as an addendum.
- Wrote down what the client's Workspace administrator has to do to turn drafting on, and what it does not change: `gmail.compose` permits sending at Google's end, so the no-send guarantee is enforced in four independent places rather than by the scope.
- Reviewed the whole delivery for security rather than scanning it: 19 findings with severities, 16 fixed and 3 accepted with stated reasons, each confirmed against the code or a live database before being written down.
- Closed the critical one: the shipped Next.js version carried a remote-code-execution advisory. Upgraded, along with three Python packages carrying 24 advisories between them. Deployed dependencies now report none.
- Gave the web tier a strict content-security policy with a per-request nonce, and HSTS - then proved in a browser that it blocks what it claims to, including a parser-inserted inline script.
- Put rate limits on every mutating route, counted atomically and failing closed, and showed 40 simultaneous attempts being granted exactly ten.
- Built the attachment scan gate C04's schema promised, where only a clean verdict passes and no scanner means failed rather than clean.
- Fenced every customer's words before they reach a model, with a delimiter the content cannot forge, and made a test enforce it for every prompt in the codebase.
- Made a data-subject request answerable: an export that gives a person their own data while redacting everybody else, and an erasure that destroys the content and keeps the record of who decided what. Verified 62 of 62 checks against a real database.
- Drilled recovery rather than assuming it: the migration chain rolled back to nothing and reapplied with every column and all 300 constraints identical, and a dump destroyed and restored with content byte-identical and every guard still enforcing. 23 of 23 checks.
- Found that CI had been four phases behind the Makefile, so the rules engine, gateway, triage and review code was unchecked there while every local run passed. CI now calls the Makefile, and a test names the defect it prevents.
- Recorded full C13 evidence in `CLIENT_C13_TEST_REPORT.md`, with the threat model, findings and privacy position in `docs/`.
- Prepared the pilot without running it: acceptance criteria with six role walkthroughs, an AI performance report, support and escalation, a production go/no-go, and four new runbook procedures.
- Named Observe Mode, made it reportable from outside, and gave it six exit criteria that live in the code and in the document the client signs - with a test that fails if the two disagree.
- Measured what could be measured: 45 realistic conversations, six safety invariants holding, 42.2% reaching a person - and said plainly that the offline accuracy figure of 1.00 is circular and must not be quoted as a result.
- Injected 82 deliberate defects across both phases and confirmed every one is caught - and fixed the nine real test gaps that exercise found, including an assertion satisfied by a comment, a percentile test whose fixture was already sorted, and a nonce that no test required to be used.
- Built the two tools C15's gate names but never defined - 26 read-only post-deploy smoke checks and an access review that separates what is wrong now from what needs a decision - and exercised both against a live local stack, in both directions.
- Wrote the handover as a checklist with empty signatures rather than a statement of readiness: 28 rows, four training sessions, and the rollback and support boundaries in writing.
- Said plainly, in the first line of the C15 report, that its gate is **not met** and cannot be met without a deployment - rather than claiming readiness because the checklist exists.
- Performed no cloud deployment, resource creation, Gmail connection, production-data mutation, or paid-model call.

## C00 artifacts

- `CLIENT_SCOPE.md`
- `DECISIONS.md`
- `CLIENT_C00_GAP_REPORT.md`
- `CLIENT_HANDOFF.md`
- `PROGRESS.md`

Planning inputs:

- `RESOLVEFLOW_EXECUTION_TRACKS.md`
- `RESOLVEFLOW_CLIENT_DEDICATED_PLAN.md`

## C01 artifacts

- `apps/web`
- `app/api`
- `contracts/openapi/v1.json`
- `compose.client.yml` and both service Dockerfiles
- `.github/workflows/ci.yml`
- `CLIENT_C01_TEST_REPORT.md` and `docs/PRODUCTION_FOUNDATION.md`

## C02 artifacts

- `infra/terraform/` - `bootstrap`, `envs/staging`, `envs/production`, and 13 modules
- `infra/docs/INFRASTRUCTURE.md` and `infra/docs/RUNBOOK.md`
- `scripts/validate_infra.py` and `tests/test_infra_definition.py`
- `CLIENT_C02_TEST_REPORT.md`
- `make infra-check` and the CI `infrastructure` job

## C03 artifacts

- `migrations/versions/20260915_0002_identity.py`
- `apps/web/src/lib/authz/`, `apps/web/src/lib/identity/`, `apps/web/src/lib/auth/`, `apps/web/src/lib/audit/`, `apps/web/src/lib/bff/`
- `apps/web/src/middleware.ts`, `apps/web/src/app/signin`, `access-denied`, `workspace`, `api/auth/[...nextauth]`, `api/workspace/capabilities`
- `scripts/bootstrap_organization.py`
- `apps/web/tests/{authz,identity,boundary}.test.mjs`, `tests/test_bootstrap_organization.py`
- `CLIENT_C03_TEST_REPORT.md`

## C04 artifacts

- `migrations/versions/20260915_0003_operations.py` and `20260915_0004_knowledge_governance.py`
- `app/retention.py`
- `tests/test_schema_invariants.py`, `tests/test_retention.py`, rewritten `tests/test_migration_foundation.py`
- `compose.client.yml` now uses `pgvector/pgvector:pg16` for local development
- `CLIENT_C04_TEST_REPORT.md`

## C05 artifacts

- `app/knowledge/` - extraction, chunking, embeddings, repository, retrieval, ingestion, evaluation
- `migrations/versions/20260915_0005_article_search_terms.py`
- `scripts/compare_retrieval.py` and `make retrieval-check`
- `app/fixtures/retrieval_cases.json`
- `tests/test_knowledge_ingestion.py`, `tests/test_retrieval_scoring.py`
- `CLIENT_C05_TEST_REPORT.md`

## C06 artifacts

- `app/mailbox/` - scopes, vault, Google client, connection lifecycle, access, stores
- `migrations/versions/20260915_0006_mailbox_credentials.py`
- `app/api/mailboxes.py` and the mailbox contracts in `app/api/contracts.py`
- `apps/web/src/app/api/mailboxes/` routes and `apps/web/src/lib/mailbox/redirects.ts`
- `docs/MAILBOX_CONNECTION_MODES.md`; keyring procedures in `infra/docs/RUNBOOK.md`
- `tests/test_mailbox_connection.py`, `tests/test_mailbox_security.py`, `tests/test_api_mailboxes.py`, `apps/web/tests/mailbox.test.mjs`
- `CLIENT_C06_TEST_REPORT.md`

## C07 artifacts

- `app/ingestion/` - pubsub, history, normalize, gmail, service, records, stores
- `tests/test_ingestion_parsing.py`, `tests/test_ingestion_recovery.py`
- `CLIENT_C07_TEST_REPORT.md`
- No migration: ingestion writes into the C04 schema as designed

## C08 artifacts

- `apps/web/src/lib/workspace/` - filters, state derivation, scoped repository
- `apps/web/src/app/workspace/` - shell, overview, inbox, ticket workspace, server actions
- `apps/web/tests/workspace.test.mjs`, `apps/web/tests/contrast.test.mjs`
- `apps/web/src/app/icon.svg`; workspace palette and layout in `globals.css`
- `CLIENT_C08_TEST_REPORT.md`
- No migration: the workspace reads the C04 schema as designed

## C09 artifacts

- `app/rules/` - business hours, SLA, routing, engine, store
- `migrations/versions/20260916_0007_routing_rules.py`
- `tests/test_business_hours_sla.py`, `tests/test_rules_engine.py`, `tests/test_rules_store.py`; migration head and invariants updated
- Rule-code explanations in `apps/web/src/lib/workspace/state.ts`
- `CLIENT_C09_TEST_REPORT.md`

## C10 artifacts

- `app/gateway/` - policy, credentials, OpenAI adapter, service, stores, MVP shim
- `migrations/versions/20260916_0008_ai_gateway.py`
- `app/api/ai_settings.py` and the AI contracts in `app/api/contracts.py`
- `apps/web/src/app/workspace/settings/ai/` and `apps/web/src/lib/ai-settings/view.ts`
- `tests/gateway_fakes.py`, `tests/test_gateway_calls.py`, `tests/test_gateway_openai.py`, `tests/test_gateway_settings.py`, `tests/test_api_ai_settings.py`, `apps/web/tests/ai-settings.test.mjs`
- Add-only BYOK grant in `infra/terraform/modules/secrets`; key and approval procedures in `infra/docs/RUNBOOK.md`
- `CLIENT_C10_TEST_REPORT.md`

## C11 artifacts

- `app/triage/` - pipeline, MCP knowledge server and client, stores, Quality Check, offline provider
- `app/fixtures/quality/` - labelled dataset `v1`, thresholds `v1`, fixture corpus
- `app/api/quality.py` and the quality contracts in `app/api/contracts.py`
- `apps/web/src/app/workspace/quality/` and `apps/web/src/lib/quality/view.ts`
- `scripts/triage_demo.py`, `scripts/quality_check.py`, `make triage-demo`, `make quality-check`
- `tests/test_triage_mcp.py`, `tests/test_triage_pipeline.py`, `tests/test_triage_store.py`, `tests/test_quality.py`, `tests/test_api_quality.py`, `tests/test_triage_demo.py`, `apps/web/tests/quality.test.mjs`
- `CLIENT_C11_TEST_REPORT.md`
- No migration: C04's `triage_runs`, `actions`, and `evaluation_*` tables were designed for this

## C12 artifacts

- `app/review/` - records, access policy, service, preview, Gmail draft composer, stores
- `migrations/versions/20260917_0009_review_and_drafts.py`
- `app/api/review.py` and the review contracts in `app/api/contracts.py`
- `apps/web/src/app/workspace/tickets/[reference]/review-panel.tsx`, `review-actions.ts`, and `apps/web/src/lib/review/view.ts`
- `tests/test_review_service.py`, `tests/test_review_drafts.py`, `tests/test_review_store.py`, `tests/test_api_review.py`, `apps/web/tests/review.test.mjs`
- `CLIENT_C12_TEST_REPORT.md`

## Preserved working-tree state

- `ResolveFlow-AI-Presentation.pptx` was already deleted in the worktree and was not touched.
- The ResolveFlow planning documents were already untracked and remain preserved.
- Local `.env` and `app/data` were not modified.

## Current verified baseline

- Branch/commit: `main` at `788a4ed`, with C02 changes in the working tree.
- Test suite: 1,872 Python tests and 200 web tests pass; PostgreSQL migration rendering, Ruff, and scoped mypy checks pass across 85 source files.
- Infrastructure: `terraform fmt` clean; `terraform validate` passes for staging, production, and bootstrap; offline checks report 63 files across 13 modules with zero findings.
- Web: contract test, ESLint, TypeScript, and Next.js 16.3.4 production build pass under Node 20.20.2.
- Runtime: Next.js web/BFF foundation plus private FastAPI adapter over the preserved Python/Streamlit, MCP, retrieval, guardrail, and grounding core.
- Review: a person can edit, approve, reject, reroute, assign and resolve; every decision names the version it was made against and is recorded with its actor and reason. **Nothing is sent, and nothing can be** - there is no send call in the codebase, `sending_enabled` is constrained to false, and the credential row refuses every send-capable scope.
- Security: reviewed, with no open critical or high finding. Strict content-security policy and HSTS, rate limits on every mutating route, a request-size cap, redacted diagnostics, and an attachment gate where only a clean scan passes. `docs/SECURITY_REVIEW.md` lists what was accepted and why.
- Privacy: a data-subject export and erasure that work, verified live. Retention is per data class with legal holds; tickets and audit rows are never swept. `docs/PRIVACY.md` marks every point where the client must decide.
- Recovery: restore and rollback are drilled, not assumed, and the runbook carries the quarterly re-drill.
- Launch readiness: the smoke test and access review run on demand (`make smoke`, `make access-review`), and `docs/HANDOVER.md` is the handover checklist. Neither gate can be verified without a deployment.
- Pilot readiness: all five C14 artifacts exist and `docs/GO_NO_GO.md` says **NO-GO** - 15 items ready, 21 blocked on the client, 8 answerable only by the pilot. The critical path is four client decisions, three of which are decisions rather than work.
- Mailbox scopes: read-only by default. Drafting is available under the `read_and_draft` profile with migration 0010 applied and the mailbox reconnected - authorized by the client on 2026-10-05, built and verified, **not yet exercised against real Google**.
- Triage: the proven workflow runs on the production schema - knowledge over MCP, every model call through the gateway, the route decided deterministically, each run recorded with its evidence and cost. Quality Check measures six gates against versioned thresholds.
- AI provider: one gateway for every model call, refusing anything unapproved, unfunded, or unreadable. **No paid model has been called**, and the client's provider, models, region, budget, prices, and key are still to be supplied.
- Deployment: local production-shape Compose verified. **No cloud resource exists.** The GCP design is defined in code and has never been applied.

## Next phase

**C13 - Security, privacy, and operations**

Plan text: "Complete threat modeling, OWASP checks, prompt-injection defenses, attachment safety, rate limits, egress, scanning, rotation, retention/deletion/export, redaction, disaster recovery, incident response, and audit access." Gate: "No open critical/high issue; restore/rollback and privacy deletion pass."

Much of this exists in pieces and needs to be made whole and evidenced. The next phase should:

1. Preserve the verified C01-C12 boundaries and unrelated working-tree changes.
2. Write the threat model against what now exists, and test the defences named in it rather than asserting them.
3. Exercise retention, deletion and export end to end on real data: `app/retention.py` plans and sweeps, C04 holds the policies and legal holds, and privacy deletion has never been run against a live database.
4. Prove restore and rollback, which `infra/docs/RUNBOOK.md` documents but nothing has exercised.
5. Add the limits and hardening the earlier phases deferred: rate limits on review decisions and key replacement, error reporters configured without local-variable capture, attachment scanning, and egress controls.
6. Keep prompt-injection defences honest: untrusted content reaches the model as data, and the tests should include attempts that look like instructions.
7. Not connect a real mailbox, apply infrastructure, deploy, or call a paid model without the client's approvals.
8. Update this handoff, `PROGRESS.md`, and `DECISIONS.md`; stop at the C13 gate.

## What C12 deliberately did not do

- Sent nothing, and could not: the mailbox is read-only and the schema refuses to store a credential that could write.
- Did not enable drafting. That needs re-consent with `gmail.compose` and a migration relaxing C06's constraint, with the client's approval.
- Built no bulk approval: approving in bulk is how an unverified answer reaches a customer.
- Added no notifications; an approved draft sits in the mailbox and the conversation shows its state.
- Did not check a reviewer's own wording for grounding. Their words are recorded as theirs.
- Did not rate limit decisions; that belongs with C13's other limits.

## What C11 deliberately did not do

- Sent nothing, and built no approval: creating a provider draft from an approved answer is C12.
- Did not trigger triage from ingestion; the worker that joins C07's queue to this pipeline needs the deployment that is still blocked on client inputs.
- Did not let the workspace start a Quality Check, because a run calls the client's paid model for every case.
- Did not measure the pgvector corpus live in this phase: this host has no pgvector and Docker was unavailable. C05 verified the same retrieval function on a real pgvector instance.
- Did not change the MVP's prompts, workflow order, or grounding validator.
- Invented no thresholds as fact: the shipped values are pilot defaults, versioned so a change is visible.

## What C10 deliberately did not do

- Did not call a paid model, or send any request to OpenAI.
- Built no second provider adapter: the client has approved one provider, and the contract is ready for another.
- Did not wire the gateway into the running triage API; that switch belongs with C11's migrated pipeline.
- Built no screen for editing approvals, prices, or budgets; those are database rows with a runbook procedure until C13/C14.
- Added no scheduled release of stale reservations; it needs the worker deployment.
- Did not rate limit key replacement, which is Owner/Administrator only and audited.

## What C09 deliberately did not do

- Did not call the engine from the live triage pipeline; that is C11's migrated pipeline.
- Built no rule administration screen; rules are managed through `PostgresRuleStore`.
- Did not change the MVP guardrails. Their keyword recall limits carry over, and any model-assisted risk detection may only add codes.
- Scheduled no breach sweep; status is computed on demand until the worker is deployed.
- Invented no client rules, hours, holidays, or zones. The engine refuses to guess, and the real values are client inputs.

## What C08 deliberately did not do

- Added no ticket actions; approve, reject, reroute, and provider drafts are C12, and the draft panel says so.
- Built no quality workspace (C11) and no administration screens; their permissions are enforced but the surfaces do not exist.
- Added no per-user saved views and no push updates; refresh is pausable polling.
- Showed no provider unread state, by design.

## What C07 deliberately did not do

- Connected no real mailbox and sent no request to Google.
- Exposed no HTTP endpoint; those paths authenticate by Cloud Run OIDC and belong with the worker's deployment.
- Added no migration; the C04 schema was sufficient.
- Downloaded no attachment, triaged nothing, and wrote no per-user seen state.

## What C06 deliberately did not do

- Connected no real mailbox and sent no request to Google.
- Read no mail. Watches, history sync, and ingestion are C07.
- Built no mailbox administration screens or permission-grant endpoints; the access decision and table are enforced, the surfaces are C08.
- Did not wire refresh into a worker or automate key rotation.
- Created no path that can send, draft, label, or modify mail.

## What C05 deliberately did not do

- Built no knowledge screens. Upload, parse status, chunk preview, retrieval test, and clear-all confirmation exist as tested functions; their surfaces are C08.
- Left the MCP boundary on the MVP retrieval path. Moving the tool onto pgvector belongs with C11, where the triage graph moves with it.
- Called no paid model. Every measurement used deterministic embeddings, so the thresholds must be re-measured against real models and the client's corpus at C14.
- Created no path that can send a customer message.

## What C04 deliberately did not do

- Wrote no ingestion, retrieval, or triage code. C04 is the data model; C05 migrates retrieval and C07 fills these tables from Gmail.
- Set no retention values. Policies are rows, not defaults; the real periods are a recorded client gate.
- Built no attachment scanner, SLA calculation engine, or rule engine. Their state is modelled; the logic belongs to C13 and C09.
- Created no path that can send a customer message.

## What C03 deliberately did not do

- Configured no Google OAuth consent screen, client, or redirect URI. Those are client-owned actions needing the client's Workspace domain.
- Built no member administration screens. The invitation schema, Owner seed, and permissions exist and are enforced; the invite/change-role/revoke surfaces belong with C08.
- Added no ticket, mailbox, queue, or knowledge tables. That is C04.
- Created no path that can send a customer message; the database forbids enabling sending.

## What C02 deliberately did not do

- Applied nothing. No project, network, database, bucket, key, secret, queue, or service exists.
- Defined no domain, TLS, WAF, or IAP, because no client domain exists; recorded as a C13 decision.
- Created no secret value.

## Inputs that gate the C02 apply and later phases

- Client legal entity and confirmed reuse/IP terms.
- Client GCP project/billing administrators and approved region.
- Google Workspace domains, launch users, and mailbox inventory.
- Retention, deletion, attachment, legal-hold, SLA, and support requirements.
- Message volume/peak and attachment-size estimates.
- Approved LLM/embedding providers, regions, models, and budgets.
- Final decision on human-approved Gmail draft creation.

## Exact next instruction

```text
Execute CLIENT plan, phase C09 only, from RESOLVEFLOW_CLIENT_DEDICATED_PLAN.md.
Read CLIENT_HANDOFF.md, CLIENT_SCOPE.md, DECISIONS.md, CLIENT_C00_GAP_REPORT.md, the C01-C08 test reports, and PROGRESS.md first. Preserve unrelated worktree changes. Build on the C04 SLA policy schema and the C08 display. Compute targets in each policy's recorded time zone, keep deterministic rules overriding model output, and preserve the MVP's proven guardrail behaviour and rule codes. Prove golden rules, conflicts, time zones, SLA boundaries, and fail-safe review with tests. Stop at the C09 gate, run proportional offline checks, update the handoff/progress/decisions, and report evidence and remaining risks. Do not connect a real mailbox, apply infrastructure, deploy, create cloud resources, call paid models, or mutate production data without explicit approval.
```
