# ResolveFlow AI — C15 Verification Report

**Phase:** C15 — Production launch and handover.

**Gate:** production smoke tests, alerts, audit, backups, budgets, access
review, and handover are green.

**Gate status: NOT MET, and it cannot be met from here.** Every item in that
list is a property of a *running production system*. None exists, and creating
one needs the client's authorization, their GCP project, their billing account
and their Workspace administrator. C14's gate is also not green: no pilot has
run.

**Date:** 2026-10-05.

---

## Read this first

This report describes what was built so that C15's gate can be *verified on the
day*, and is explicit that the verification has not happened. Nothing here
should be read as "launch is ready".

C15's activities are "deploy through reviewed CI, seed the Owner securely,
connect a limited cohort, monitor, expand gradually, document
backup/restore/rotation, and hand over client-owned administration. Record
rollback and support boundaries."

| Activity | Status |
|---|---|
| Deploy through reviewed CI | **Blocked** — no authorization, no project, no billing |
| Seed the Owner securely | **Blocked** — needs a deployment and a named person |
| Connect a limited cohort | **Blocked** — needs real mailboxes and real users |
| Monitor | **Blocked** — needs a running system and a real alert recipient |
| Expand gradually | **Blocked** — needs a cohort |
| Document backup, restore, rotation | **Done** in C13, drilled 23/23 |
| Hand over client-owned administration | **Prepared**, not performed |
| Record rollback and support boundaries | **Done** |

So six of eight are blocked on the client. What this phase could do was build
the two verification tools the gate names but never defined, and write the
handover as a checklist rather than a conversation.

## Delivered

| Piece | What it is |
|---|---|
| `scripts/smoke_test.py` | 26 read-only checks across the API, the web tier and the database. Exit status is the verdict, so it can gate a release |
| `scripts/access_review.py` | Who can reach the workspace, in what role, over which mailboxes — split into findings that are wrong now and observations that need a decision |
| `docs/HANDOVER.md` | 28 checklist rows across deployment, recovery, monitoring, cost, access, audit, privacy and mail; four training sessions; rollback and support boundaries; limitations handed over knowingly |
| `make smoke`, `make access-review` | So neither tool depends on remembering its arguments |

## The smoke tests

Run immediately after a deploy. **Every check is read-only** — a smoke test
that writes to production leaves rows nobody asked for and, on a day when
something is already wrong, makes the state harder to read. Tests assert that
no statement mutates anything and that no HTTP method but GET is used.

It also deliberately does not call a paid model. Spending money on every
deploy is not a smoke test.

### Verified against a live local stack — 26 checks, PASS

A throwaway PostgreSQL 17, the real FastAPI service under
`DEPLOYMENT_PROFILE=production`, and the real Next.js production build.

| Group | Checks | Result |
|---|---|---|
| The private API | 8 | Health responds; **an unauthenticated caller is refused 401**; a wrong token is refused 401; the service credential is accepted; `automatic_sending` is false; the pilot stage is reported (`OBSERVE`); **`/openapi.json` is 404 outside local and test**; a missing organization context is refused 400 |
| The web tier | 9 | Responds; a policy with `default-src 'none'` and a nonce is sent; no `unsafe-*` allowance; framing refused; HSTS correctly absent over plain HTTP; `nosniff`; **each response gets a distinct nonce**; `/workspace` redirects an unauthenticated visitor to `/signin`; and no workspace content is served |
| The database | 9 | Reachable; schema at `20260920_0012`; both append-only triggers present; no organization has sending enabled; **no stored credential could send**; an active Owner exists; retention configured; no expired session (reported as a warning) |

One warning fired on the seeded data — an expired session still stored — which
is correct behaviour: it is a housekeeping observation, not a launch blocker,
and it does not fail the run.

### A defect this uncovered in my own check

The first version of the workspace check accepted `{200, 302, 303, 307}` and
reported `GET /workspace -> 200`, which it called a pass. urllib follows
redirects by default, so it had followed the redirect to the sign-in page and
reported *that* page's status — indistinguishable from the workspace having
been served to a visitor with no session, which would be a critical failure.

It now stops at the redirect, asserts a 3xx whose `Location` points at
`/signin`, and separately asserts that no workspace markup came back. The live
run reports `307 to /signin?next=%2Fworkspace` and 25 bytes.

## The access review

Two parts, and the split is the point. **Findings** are wrong now and fail the
run. **Observations** need a human to decide, and reporting those as failures
would train an administrator to ignore the output — which is worse than not
running it.

### Verified against a live database, both directions

**Clean workspace:** 1 active Owner, domain allowlist in force, no findings,
one observation ("has never signed in"). **Exit 0.**

**Deliberately broken workspace** — a revoked member with a live session, that
member still holding a mailbox permission, an account with no membership, an
agent with no permission, and an expired session:

```
3 finding(s) - these are wrong now:
  - leaver@acme.example is REVOKED but has a session that has not expired - delete the session
  - leaver@acme.example is REVOKED but still holds a permission on support@acme.example - remove it
  - orphan@acme.example has an account with no membership - it cannot sign in, so remove the account

3 observation(s) - these need a decision:
  - 1 expired sessions are still stored …
  - newagent@acme.example (AGENT) holds no mailbox permission, so they see nothing …
  - owner@acme.example (OWNER) has never signed in
```

**Exit 1.** Every finding fired, and the observations stayed observations.

While seeding that state, C03's schema refused a `REVOKED` membership with no
revocation timestamp — the constraint doing its job, and a reminder that the
broken states this tool looks for are harder to create than to imagine.

## Automated evidence

| Check | Result |
|---|---|
| `make check` | **exit 0** |
| Python tests | **1,916 passed** |
| Web tests | **200 passed** |
| `ruff`, `mypy` | clean on 85 source files |
| New test module | `test_launch_tooling` (44) |
| Live smoke run | 26 checks, PASS, 1 warning |
| Live access review | clean workspace exit 0; broken workspace exit 1 with all 3 findings |

## What the gate still needs, and who provides it

| Gate item | Needs |
|---|---|
| **Production smoke tests** green | A production deployment. The tool is written and verified against a live local stack |
| **Alerts** green | The five alert policies applied, with a real recipient who confirms receipt. Client: alert addresses |
| **Audit** green | Verified in production rather than locally — one `UPDATE` on `audit_events` being refused. The trigger is drilled |
| **Backups** green | Cloud SQL PITR on the real instance, plus one restore actually performed. Client: project and billing |
| **Budgets** green | A GCP budget and the product's monthly AI budget. Client: billing account, provider approval |
| **Access review** green | A deployment with the client's real people in it. The tool exits zero on a clean workspace |
| **Handover** green | 28 checklist rows, four training sessions, and four signatures. Client: participants and a date |

## Residual risks and open items

| Item | Status |
|---|---|
| **Nothing is deployed, so the gate is unverified** | The tools exist and are exercised locally. They have never seen production, and a local stack is not production |
| C14's gate is not green either | No pilot has run. C15 cannot meaningfully precede it |
| The smoke test's expected revision is pinned | Tied by a test to the migration chain's head, so it cannot silently pass a stale deployment |
| Alerts have never fired | Triggering one deliberately is checklist row 2.1, and it is the only way to know a recipient exists |
| A restore has been drilled, not performed on a real instance | Row 1.3. A drill on a throwaway database is evidence about the procedure, not about their instance |
| No cohort expansion has happened | Rows 5.1–5.4. The product does not gate by cohort; "limited" means the client connects one mailbox and invites a few people |
| Single-engineer support, no 24/7 rota | Stated in `docs/SUPPORT.md` and `docs/HANDOVER.md` §4 rather than implied |
| Fifteen phases remain uncommitted | Strongly recommended now |
