# ResolveFlow AI - C03 Verification Report

**Phase:** C03 - Identity, one organization, and RBAC

**Result:** GREEN

**Date:** 15 September 2026

## Scope and safety

No cloud resource was created, read, or mutated. No Google account was authenticated and no OAuth consent screen was configured. No Gmail mailbox was connected. No paid model was called. No production data was touched.

A local PostgreSQL 16 container was started from `compose.client.yml` to verify that the migration applies and that its constraints behave, then stopped and its volumes removed. It held synthetic data only (`acme.example`).

## Delivered

- Migration `20260915_0002_identity`: organizations, approved domains, users, accounts, sessions, verification tokens, departments, memberships, department memberships, invitations, and append-only audit events. Every tenant-owned table carries `organization_id`.
- Auth.js Google Workspace sign-in with database sessions, an approved-domain allowlist, and a required invited membership.
- A pure authorization core: six roles, 25 permissions, one `authorize` decision function with stable reason codes.
- Server-side guards (`requirePageAccess`, `requireAccess`) that re-derive identity from the database on every protected request.
- Middleware that filters unauthenticated traffic and sets security headers without making role decisions.
- Server-derived actor context on the internal Python boundary, retiring the C01 placeholder.
- `scripts/bootstrap_organization.py`: a one-time, idempotent, refusing bootstrap for the single organization and its Owner.
- Sign-in, access-denied, and workspace screens with keyboard-visible focus and responsive layout.
- 108 new automated tests (Python 74 → 121, web 1 → 62).

## Automated gate evidence

All commands were run and passed.

| Check | Result |
|---|---|
| Python suite | **121 passed** in 46.31s (was 74 at C02) |
| Web suite | **62 passed** (was 1 at C02) |
| Ruff | passed, incl. the new bootstrap script |
| mypy | no issues in 8 source files |
| Migration chain | one head `20260915_0002`; renders as PostgreSQL without a database |
| ESLint | passed with zero warnings |
| TypeScript | `tsc --noEmit` passed |
| Next.js production build | passed; 8 routes plus middleware |
| Contract drift | regenerated and verified; still no send endpoint |
| Infrastructure checks | 63 files, 13 modules, 0 findings |
| `terraform fmt` / `validate` | clean; staging and production both valid |

### Adversarial coverage - the C03 gate

The gate is "route, API, object, and role tests prove unauthorized access fails". What is actually asserted:

**Role** (22 tests). Every role against every permission. Only Owner may transfer ownership or set retention. An Agent is refused all nine administrative permissions. A Knowledge Manager owns the corpus and is refused every ticket permission. An Auditor is refused all 15 write permissions by a check independent of the matrix. An unknown role holds nothing; an unknown permission is denied rather than treated as ungated. A permission-count snapshot per role means widening a role must change an explicit expectation.

**Object**. No role, *including Owner*, may act on a resource in another organization. A department-scoped role cannot reach another department's work. An agent with no department assignment cannot reach a departmental resource. A cross-tenant reference is reported as such even when the role separately lacks the permission.

**Session** (26 tests). Expired, revoked, unknown, empty, and whitespace tokens; a session for a deleted user; a revoked membership with a live session; invited and suspended memberships; an unbootstrapped deployment; a membership belonging to another organization; an unrecognised role or status returned by the repository. Every case yields no context.

**Sign-in**. An approved domain alone is never sufficient. Subdomain and suffix spoofs (`client.example.attacker.net`, `sub.client.example`) are refused. Unverified emails, revoked and suspended memberships, non-Google providers, and malformed addresses are refused. An empty allowlist admits nobody.

**Route and API** (13 tests). Protected sources are checked for a guard and against reading identity from the request. The middleware's public-path list is pinned. The middleware is asserted *not* to import the authorization or identity modules. Every server-only module is asserted to carry its `server-only` guard. Denial messages are asserted not to reveal whether a resource exists.

**Audit**. Credential- and content-shaped metadata keys are redacted; long values truncated; objects rejected; source addresses hashed with a per-environment salt and never stored raw.

**Bootstrap** (33 tests). Invalid slugs, domains, emails, and department slugs are refused. An Owner outside the approved domains is refused, because that would produce a deployment nobody can sign in to. The executor refuses when an organization already exists, attempting nothing further. Every value is parameterised.

### Live database verification

Run against a local PostgreSQL 16 container, then torn down:

| Behaviour | Result |
|---|---|
| Migration applies to an empty database | 12 tables created |
| Downgrade to the previous revision | all tables and all three enums removed cleanly |
| Re-upgrade after downgrade | returns to head |
| Bootstrap creates org, domain, 4 departments, Owner, audit event | 1/1/4/1/1 rows |
| Bootstrap a second time | refused; organization count stays 1 |
| A second ACTIVE Owner | rejected by `uq_memberships_single_active_owner` |
| A second Admin | accepted |
| The same user twice in one organization | rejected |
| `UPDATE organizations SET sending_enabled = true` | rejected by check constraint |
| `UPDATE audit_events` | rejected: "audit_events is append-only (attempted UPDATE)" |
| `DELETE FROM audit_events` | rejected: "append-only (attempted DELETE)" |
| `INSERT INTO audit_events` | accepted |
| An unknown audit outcome | rejected |
| A mixed-case user email | rejected |
| Role `'SUPERUSER'` | rejected by the enum |
| Inviting an OWNER | rejected by `ck_invitations_owner_is_not_invitable` |
| Inviting an AGENT | accepted |
| A token hash that is not 64 hex characters | rejected |
| Two pending invitations for one email | rejected |

### Defects the verification caught

Recorded because they are the evidence these checks work.

1. **A migration that rendered valid SQL but would not apply.** The enum types were created explicitly *and* again implicitly by the first `create_table` that referenced them, so a real apply failed with `type "membership_role" already exists`. Offline SQL rendering could not see this; only applying to PostgreSQL could. Fixed with `create_type=False` on the column-bound instances.
2. **A bootstrap that silently rolled back.** `:metadata::jsonb` is parsed by SQLAlchemy as part of the bind parameter name, so the value was never bound and the whole transaction rolled back. The recording fake used by the offline tests cannot catch this because it never binds anything. Fixed with `CAST(:metadata AS jsonb)`, and a regression test now asserts no statement contains `::`.
3. **An inconclusive test run mistaken for a passing one.** Because of defect 2 the tables were empty, so the first constraint run reported `UPDATE 0` and `DELETE 0` - which looks like "the constraint held" but proves nothing. Re-run with rows present, and the constraints then genuinely rejected every case.
4. **A build that required a database.** `PostgresAdapter(getPool())` at module scope ran during `next build`, which collects page data by importing route modules. Fixed by building the Auth.js config per request.
5. **An authorization ordering that produced inconsistent audit signals.** Checking the role before the resource's organization meant the same cross-tenant attempt was reported differently depending on the caller's role. Reordered, and decided deliberately in C-D029.
6. **A gap between C02 infrastructure and C03 code.** The web service needed `AUDIT_IP_SALT` and `GOOGLE_OAUTH_CLIENT_ID`, which the Terraform definition did not provide. Added a secret container and a variable; `terraform validate` and the offline checks still pass.

## What C03 deliberately did not do

- **No OAuth consent configuration.** Creating the Google client, setting redirect URIs, and verifying the app are client-owned actions requiring the client's Workspace domain.
- **No member administration UI.** The invitation *schema*, the Owner seed, and the permissions exist; invite, change-role, and revoke screens belong with C08's administration surfaces. The permissions are already enforced.
- **No tickets, mailboxes, queues, or knowledge tables.** C04 extends this schema rather than replacing it.
- **No sending path.** The organization table forbids enabling it at the database level.

## Residual risks

| Risk | Treatment |
|---|---|
| The sign-in flow has never run against real Google OAuth; only the policy that gates it is tested | End-to-end sign-in is a C14 staging-pilot item and needs the client's OAuth client |
| Auth.js 5 is a beta release | Pinned exactly at `5.0.0-beta.32`, which declares Next 16 support; the production build passes. Revisit at C13 |
| Identity resolution adds a database read per protected request | Deliberate (C-D030). Watch p95 during the C14 pilot; a short in-request cache already deduplicates within one render |
| Audit write failures are swallowed | Deliberate so a failing sink cannot block sign-in. C12 and C13 must decide whether their actions need hard-fail auditing |
| Placeholder departments are seeded when none are supplied | The bootstrap plan prints a warning naming `CLIENT_SCOPE.md`; real departments are a recorded client gate |
| Route guards are verified by source-level assertions, not by driving a browser | Full browser, keyboard, and contrast journeys belong to C08 |
| The session cookie name is pinned; a future Auth.js change could diverge | Asserted by a test that config, guard, and middleware all use the one shared helper |

## Preserved working-tree state

`ResolveFlow-AI-Presentation.pptx` remained deleted and untouched. The untracked planning documents, local `.env`, and `app/data` were not modified. Docker volumes created for verification were removed.

## Gate conclusion

C03 meets its gate: unauthorized access fails at the route, API, object, and role levels, and each failure is proven by a test. Identity, organization, role, and department are derived server-side on every request and cannot be influenced by the browser. C04 may begin.
