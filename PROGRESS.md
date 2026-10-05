# ResolveFlow AI - Client Track Progress

| Phase | Status | Gate evidence |
|---|---|---|
| C00 - Contract, environment, repository audit | **GREEN** | Scope, decisions, gap report, risks, ownership, unknown inputs, secret posture, and handoff recorded; 43 tests pass |
| C01 - Production repository foundation | **GREEN** | Next.js/FastAPI foundation, migrations, versioned contract, local orchestration, CI, and both containers verified; 50 Python tests plus web checks pass |
| C02 - Client-owned GCP foundation | **GREEN (definition)** | Terraform for separate staging/production defined and verified offline: `terraform fmt` clean, `terraform validate` passes against google provider 6.50.0 for all three root modules, 24 infrastructure tests pass. Nothing applied; no cloud resource exists |
| C03 - Identity, one organization, RBAC | **GREEN** | Auth.js Workspace login, database sessions, domain allowlist, invited membership, six roles, server-derived authorization, bootstrap Owner, append-only audit. 121 Python + 62 web tests; migration applied, downgraded, and constraint-tested on live PostgreSQL |
| C04 - Durable data model and audit | **GREEN** | 37 tables, 108 indexes, 74 check constraints, 3 triggers. Applied, downgraded, re-upgraded, constraint-tested, retention-swept, and dump/restored on live PostgreSQL 16 + pgvector 0.8.6. 162 Python + 62 web tests |
| C05 - Knowledge ingestion and production RAG | **GREEN** | PDF/Markdown/text/HTML/CSV ingestion, exact citation offsets, pgvector + PostgreSQL full-text hybrid retrieval with the MVP rerank preserved. Labelled Recall@1/@3/MRR all 1.000 on both paths, zero regression. 246 Python + 62 web tests |
| C06 - Gmail connection and permissions | **GREEN** | Read-only OAuth with scopes checked both ways, AES-256-GCM credentials bound to one mailbox, verified account identity, single-use attempts, classified refresh failures. Gate cases proven offline and end to end on PostgreSQL; 16/16 constraints reject on their named reason. 410 Python + 77 web tests. No real mailbox connected |
| C07 - Reliable multi-mailbox ingestion | **GREEN** | Watches, history cursors, reconciliation, idempotent threads/messages/tickets, retries, dead letters, attachment policy. All seven recovery scenarios proven offline and on PostgreSQL; 50 mailboxes simulated with a replay adding nothing. 505 Python + 77 web tests. No HTTP endpoint yet; no real mailbox |
| C08 - Unified operations workspace | **GREEN** | Overview, unified inbox with saved views and URL-backed filters, ticket workspace, bulk assignment, derived per-person unseen state. Browser, keyboard, contrast, and responsive checks pass; authorization verified through the interface as two different people. 126 web tests |
| C09 - Rules, routing, SLA, guardrails | **GREEN** | Versioned client rules in a closed language, VIP, holidays, specificity-ranked SLA policies, and DST-correct working-time arithmetic in each policy's zone. MVP guardrails run first and rules can only narrow the route; precedence pinned against `app.graph.decide` in 224 cases. Conflicts and invalid rules fail to a person. 14/14 injected defects caught; 49/49 live PostgreSQL checks. 1,016 Python + 126 web tests. Client's actual rules still required |
| C10 - Client BYOK AI gateway | **GREEN** | One contract for every provider: approvals and region enforced before any call, worst-case cost reserved atomically against a monthly budget, key read from Secret Manager and never stored elsewhere, bounded retries, one repair, and fallback only to approved models in the same region. Every gate case fails to a person with a code an operator can read. 29/29 injected defects caught; 67/67 live PostgreSQL checks; browser-verified as three roles. No paid model was called |
| C11 - Triage, grounded drafting, quality | **GREEN** | The proven workflow running on the production schema: C05 knowledge over MCP, C10 gateway for every model call, C09 rules deciding the route again and only narrowing it, and one transaction recording the run, its action, its routing and its targets. Quality Check measures six gates against versioned thresholds and records the corpus it measured. 20/20 injected defects caught; 26/26 live PostgreSQL checks; demonstration and quality page browser-verified. 1,344 Python + 158 web tests. No paid model called |
| C12 - Human review and provider drafts | **GREEN** | Edit, approve, reject, reroute, assign and resolve through one service: every decision names the ticket version it was made against, carries an idempotency key, and is recorded with its actor, reason and both versions - refusals included. The Gmail draft path is built and tested. **No path sends anything.** 24/24 injected defects caught; 62/62 live PostgreSQL checks; eight concurrent approvals produced one change and seven recorded conflicts |
| C12a - Compose scope (client-authorized) | **GREEN** | Two scope profiles, read-only by default: `read_and_draft` asks for `gmail.compose` as an *optional* scope, and migration 0010 narrows C06's constraint so compose is storable while all seven send-capable scopes stay refused at the database. Access tokens are leased from C06 for 45 minutes, never stored. The transport is wired only where the profile, the OAuth client and the vault all agree. 28/29 injected defects caught (one equivalent mutant); 11/11 live constraint checks plus a negative control at 0009 and a refused downgrade. **No mailbox re-consented** - that needs the client's Workspace admin. 1,584 Python + 181 web tests |
| C13 - Security, privacy, operations | **GREEN** | A threat model and a 19-finding security review: 1 critical, 6 high, 7 medium, 5 low; **16 fixed, 3 accepted** (low or build-time only). Strict content-security policy with a per-request nonce and HSTS, browser-verified with negative controls. Rate limits on every mutating route, atomic and failing closed - 40 concurrent attempts granted exactly 10. A 256 KiB body cap before parsing. One redaction rule set for diagnostics. The attachment scan gate C04 promised, where only CLEAN passes. Untrusted content fenced in every prompt, AST-enforced. Data-subject export and erasure: **62/62 live**. Restore and rollback drilled: **23/23 live**, including that every constraint still enforces after a restore. `next` upgraded for a critical RCE; PyJWT, pypdf and urllib3 for 24 advisories; production dependencies now report zero. CI now calls the Makefile after drifting four phases behind it. 53/53 injected defects caught |
| C14 - Staging pilot and acceptance | **AMBER** | All five gate artifacts exist: UAT criteria (unsigned - the client's act), AI performance report, support and escalation, production go/no-go, and four new runbook procedures. Observe Mode is now named, reportable through `/v1/capabilities`, and carries six exit criteria held in code and mirrored in the UAT document by a sync test. A pilot acceptance run over 45 realistic conversations: **6/6 safety invariants**, 42.2% reaching a person, with latency and cost reported as simulation rather than prediction. **No pilot has run**: staging, a mailbox, a provider and the client's labelled cases are all blocked on client authorization. Go/no-go records **NO-GO** with 21 blocked and 8 pending-pilot items. 29/29 injected defects caught; 1,872 Python + 200 web tests |
| C15 - Production launch and handover | **AMBER** | The gate's verification tooling is built and exercised against a live local stack: `scripts/smoke_test.py` (26 read-only checks across API, web and database - **PASS**, with `/openapi.json` 404, an unauthenticated caller refused, a distinct nonce per response, and no send-capable credential storable) and `scripts/access_review.py` (**exit 0** on a clean workspace, **exit 1** with all three findings on a deliberately broken one). `docs/HANDOVER.md` carries 28 checklist rows, four training sessions, and the rollback and support boundaries in writing, with empty signatures. **The gate is NOT met**: every item in it is a property of a running production system, six of eight activities are blocked on client authorization, and C14's pilot has not run |

## Current gate

C09 through C13 are green. C14 and C15 are amber, for the same reason: their artifacts and tooling are built and verified as far as a local stack allows, and their gates are properties of systems that do not exist. C14 needs a pilot on real mail; C15 needs a production deployment. Both wait on the client authorizing staging, approving a provider, connecting a mailbox and supplying labelled cases.

**Everything within delivery's control is done.** `docs/GO_NO_GO.md` lists 44 readiness items: 15 ready, 21 blocked on a client decision or input, 8 answerable only by running the pilot. The recommendation is **NO-GO**, with the critical path stated.

**Nothing is sent, and nothing can be.** An approval creates a draft for a person to send. On this deployment, in Observe Mode, it does not even do that: the default scope profile is read-only, so an approval is recorded and no draft is created. `organizations.sending_enabled` remains constrained to false, no code path references a send endpoint, and there is no pilot stage in which the product sends - `automatic_sending` is a published constant, not a setting.

**No paid model has been called.** The gateway refuses every call until the client supplies a provider, model, region, monthly budget, agreed prices, and their own API key.

Connecting any real mailbox still requires the client's Internal OAuth client, a named mailbox, and explicit approval; ingestion additionally needs the worker deployed before anything can receive a notification, and that same worker is what would trigger triage on arrival.

C02's **definition** gate is green. The plan's C02 gate also requires "staging deploys with private backend/worker/database access and documented rollback" - that half is **not** met and cannot be, because applying requires client project IDs, region, billing, and explicit authorization that do not yet exist. Rollback is documented in `infra/docs/RUNBOOK.md` but has not been exercised.

No cloud deployment, resource creation, Gmail connection, paid-model call, or production-data mutation has occurred or is authorized. C03 may begin in parallel, since identity and RBAC are application work that does not depend on an applied environment.

## Evidence pointers

- Scope and client inputs: `CLIENT_SCOPE.md`
- Production decisions: `DECISIONS.md`
- Detailed repository evidence and gaps: `CLIENT_C00_GAP_REPORT.md`
- C01 verification report: `CLIENT_C01_TEST_REPORT.md`
- C02 verification report: `CLIENT_C02_TEST_REPORT.md`
- C03 verification report: `CLIENT_C03_TEST_REPORT.md`
- C04 verification report: `CLIENT_C04_TEST_REPORT.md`
- C05 verification report: `CLIENT_C05_TEST_REPORT.md`
- C06 verification report: `CLIENT_C06_TEST_REPORT.md`
- C07 verification report: `CLIENT_C07_TEST_REPORT.md`
- C08 verification report: `CLIENT_C08_TEST_REPORT.md`
- C09 verification report: `CLIENT_C09_TEST_REPORT.md`
- C10 verification report: `CLIENT_C10_TEST_REPORT.md`
- C11 verification report: `CLIENT_C11_TEST_REPORT.md`
- C12 verification report: `CLIENT_C12_TEST_REPORT.md`
- C13 verification report: `CLIENT_C13_TEST_REPORT.md`
- C14 verification report: `CLIENT_C14_TEST_REPORT.md`
- C15 verification report: `CLIENT_C15_TEST_REPORT.md`
- Threat model and trust boundaries: `docs/THREAT_MODEL.md`
- Security findings, fixed and accepted: `docs/SECURITY_REVIEW.md`
- What is held, and how to remove it: `docs/PRIVACY.md`
- Acceptance criteria for the pilot: `docs/UAT_CRITERIA.md`
- What has and has not been measured: `docs/AI_PERFORMANCE_REPORT.md`
- Support, severities and escalation: `docs/SUPPORT.md`
- Production readiness: `docs/GO_NO_GO.md`
- Client-owned administration handover: `docs/HANDOVER.md`
- Incident response: `infra/docs/INCIDENT_RESPONSE.md`
- Mailbox connection modes for the client's administrator: `docs/MAILBOX_CONNECTION_MODES.md`
- Infrastructure architecture and required client inputs: `infra/docs/INFRASTRUCTURE.md`
- Apply, secrets, rollback, and restore procedures: `infra/docs/RUNBOOK.md`
- Continuation context: `CLIENT_HANDOFF.md`
- Phase definitions: `RESOLVEFLOW_CLIENT_DEDICATED_PLAN.md`
