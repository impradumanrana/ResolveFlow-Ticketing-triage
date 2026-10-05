# ResolveFlow AI — C13 Verification Report

**Phase:** C13 — Security, privacy, and operations.

**Gate:** no open critical or high issue; restore/rollback and privacy deletion
pass. **Both met**, and the evidence is below.

**Date:** 2026-10-05.

---

## Scope and safety

- No cloud resource was created. No mailbox was connected. **No paid model was
  called.** No production data was touched, because none exists.
- Every live check ran against a throwaway PostgreSQL 17.10 created for the
  purpose and destroyed afterwards. Docker remained unavailable, so three
  pgvector statements were excluded from the chain, each asserted to occur
  exactly once so a silent change to the chain fails rather than being skipped.
- Unrelated worktree changes were preserved.

## Delivered

| Piece | What it is |
|---|---|
| `docs/THREAT_MODEL.md` | Trust boundaries, the actors defended against, threats per boundary, and the assumptions the model rests on |
| `docs/SECURITY_REVIEW.md` | 19 findings with severities, each confirmed against code or a live database before being written down |
| `docs/PRIVACY.md` | What is held, where it goes, how long it stays, and how to remove it |
| `infra/docs/INCIDENT_RESPONSE.md` | Containment first, then triage by symptom, then the breach path with its deadlines |
| `app/security/redaction.py` | One redaction rule set for logs, traces and audit metadata |
| `app/security/untrusted.py` | The instruction/data boundary for every model prompt |
| `app/security/attachments.py` | The scan gate C04's schema promised and deferred here |
| `app/security/rate_limit.py` | Seven policies, one atomic counter, failing closed |
| `app/api/limits.py` | The limits applied at the boundary, plus a 256 KiB body cap |
| `app/privacy.py` | Data-subject export and erasure |
| `apps/web/src/lib/security/headers.ts` | A strict content-security policy and HSTS, as testable data |
| `migrations/…_0011_rate_limits.py`, `…_0012_erasure_records.py` | The two tables those need |

## The gate

### No open critical or high issue

19 findings: 1 critical, 6 high, 7 medium, 5 low. **16 fixed, 3 accepted** —
all three low or build-time only, each with the reason and what would change
the decision. Full register in `docs/SECURITY_REVIEW.md`; the headlines:

| Severity | Finding | Outcome |
|---|---|---|
| Critical | Remote code execution advisory in the shipped `next@16.3.4` (`next/og`) | Upgraded to 16.3.8. Production dependencies now report **zero** advisories |
| High | No content-security policy and no HSTS | Strict policy with a per-request nonce; browser-verified with negative controls |
| High | No rate limiting anywhere | Seven policies on every mutating route; verified under 40-way concurrency |
| High | No request body size limit | 256 KiB, enforced before parsing, counting what arrives |
| High | The attachment scan gate was promised but absent | One gate, `CLEAN` only; no scanner means `FAILED`, never `CLEAN` |
| High | No data-subject export or erasure | Both, verified 62/62 live |
| High | 24 known advisories in Python dependencies | PyJWT, pypdf and urllib3 upgraded; `pip-audit` clean |

### Privacy deletion passes — 62/62 live checks

Against real PostgreSQL, with a seeded conversation, a third party copied in,
and the same person's data at a second client.

| Group | Checks | Result |
|---|---|---|
| Finding the subject | 4 | Ticket, both messages including the cc, the thread; the other client's copy found only under that organization |
| Export | 8 | Own words and address included unredacted; attachment metadata included; **the third party's address redacted**; no other organization's data present |
| Refusals | 7 | An Agent refused; an Owner of another organization refused; three malformed addresses refused; **a legal hold blocks erasure, and nothing was erased while it stood** |
| Content destroyed | 19 | Eight columns tombstoned; the subject's address gone from every header and replaced with a reserved `.invalid` address; **the third party's address untouched**; the trace and grounding details no longer quote the customer; the VIP row deleted; the draft checksum recomputed |
| History survives | 4 | Reference, version, status and route intact; the conversation marked deleted; message rows still present so counts are not falsified |
| The record | 8 | Identified by digest, holds no address, names who ordered it and why; an audit event written with the digest only; **the record is append-only** |
| Idempotency and scoping | 4 | A repeat request reports `ALREADY_ERASED`; an unknown subject is reported, not erased; the other client's data and the same address there untouched |

### Restore and rollback pass — 23/23 live checks

| Group | Checks | Result |
|---|---|---|
| Rollback | 10 | The chain applies, lands at head, **every downgrade runs head to base**, the schema is left empty, enum types and functions are dropped too, the chain re-applies, and the same 49 tables return with **every column and all 300 constraints identical** |
| Restore | 9 | `pg_dump` to a custom archive, the database destroyed and confirmed gone, `pg_restore` clean, **every row count matches**, ticket content and versions byte-identical by fingerprint, all 4 triggers restored, and the restored database knows its revision |
| Still enforcing afterwards | 4 | `audit_events` and `erasure_records` still append-only; the send-scope constraint still refuses; sending still disabled at the organization level |

A restore that has never been tested is a backup, not a recovery plan. The
runbook now carries this as a quarterly drill.

## Automated evidence

| Check | Result |
|---|---|
| Python tests | **1,872 passed** |
| Web tests | **200 passed** |
| `ruff`, `mypy` | clean on 85 source files |
| `scripts/validate_infra.py` | 63 files across 13 modules |
| `pip-audit` | no known vulnerabilities |
| `npm audit --omit=dev` | 0 vulnerabilities |
| New test modules | `test_security_redaction` (55), `test_security_prompts` (49), `test_privacy` (53), `test_security_attachments` (31), `test_security_rate_limit` (29), `test_api_limits` (18) |

### Live rate limiter — 18/18

40 concurrent attempts against a 10-per-minute policy **granted exactly 10**,
refused 30, and left one row accounting for all 40. The key is stored as a
SHA-256 with the identifier absent; the refused request is still counted; the
window is the clock-aligned bucket; keys, organizations and policies have
separate allowances; and the three table constraints each refuse what they
promise to.

### Mutation check

**53 mutants, all 53 caught.** Each is one edit that makes a control wrong in a
way that matters, applied to the real source and reverted afterwards.

Caught include: an email, a bearer token or a Google access token surviving
redaction; every long number being called a card; a secret key being printed;
an exception chain not being walked; content closing its own fence; the fence
token becoming predictable; the untrusted-data rule being dropped from the
notice; the classification prompt losing its fence; an unscanned or deleted
attachment becoming readable; "no scanner" meaning clean; an infected verdict
being overwritten; the window losing its clock alignment; the limiter key
stored in the clear; the allowance off by one in either direction; a limiter
that cannot count failing open; an oversized or lying body being accepted; an
Admin being allowed to erase; a legal hold no longer blocking; an erasure
statement losing its tenant scope; the erasure record storing the address; an
export disclosing other people's addresses; the policy allowing inline
scripts; HSTS being dropped or sent over plain HTTP; the nonce becoming a
constant; and CI's dependency audit or secret scan being disabled.

**Four of the first run's survivors were real test gaps, and were fixed:**

1. The statement-backed limiter's boundary was never driven offline — only the
   in-memory one was — so an off-by-one in `count > limit` survived. Now
   exercised with a counting fake engine, on both sides of the boundary.
2. `assert response.status_code in {400, 413, 422}` passed even with the
   byte-counting removed. Now asserted exactly, with the reason the lying-length
   case answers 400 rather than 413 recorded in the test.
3. The body-size limit was asserted only from below, so a mutant raising it to
   256 MiB passed. Now bounded on both sides.
4. `assert "gitleaks" in workflow` was satisfied by a *comment* above the step.
   Now asserts the `uses:` line.

A fifth, the content-security policy's nonce, survived a later run: every test
called `contentSecurityPolicy` directly, so `securityHeaders` ignoring its
nonce argument went unnoticed — which would have made every response share one
predictable value. Two web tests now cover it.

## Defects found during this phase

Five were found by review, four by running the thing.

1. **The classification prompt had no instruction boundary.** The drafting
   prompt told the model the ticket was untrusted data; the classifier passed
   the raw body with no such statement. Fixed for every prompt site, enforced
   by an AST test.
2. **A rate-limit constraint compared two clocks.** `window_start <=
   updated_at` put the application's clock against the database's. A few
   seconds of skew would make a fail-closed limiter refuse legitimate work.
   **Found by the live run.** Removed, with the reasoning recorded in the
   migration.
3. **The erasure tombstone violated a schema constraint.** `triage_runs.trace`
   is constrained to a JSON array; the erasure wrote an object, so erasing any
   triaged conversation would have failed mid-transaction. **Found by the live
   run.**
4. **A transport with no token provider called Gmail with an empty bearer**,
   reporting the resulting 401 as the mailbox's fault.
5. **CI was four phases behind the Makefile.** Its inline path lists never
   advanced, so C09–C12 code was unchecked in CI while every local run passed.
6. **Disconnecting a mailbox had no rate limit** — found by the test written to
   assert that every mutating route names a policy.
7. **Address validation accepted `@example.net`** (no local part).
8. **Two tests that could not fail**: one asserted on the source text of
   `_compose` rather than its behaviour; one covered a branch a second guard
   also caught. Both found by mutation testing and replaced.
9. **Three tests named the old C06 constraint** and passed off migration 0006
   while the live schema had moved on. Rewritten against the rendered chain.

## Residual risks

| Item | Status |
|---|---|
| `strict-dynamic` trusts scripts a running script creates | The policy constrains injected markup, not an attacker already executing JavaScript. Removing it breaks the framework's chunk loading |
| No attachment scanner is integrated | The gate refuses everything, which is the safe direction, and no code path fetches attachment bytes. The test fails the day a download is added without the gate |
| Prompt injection can still shift a classification | Bounded by a closed output: a fixed enum, verbatim evidence quoting, deterministic re-routing, and no send path |
| Build-time ESLint advisories remain | 5 high, dev-only, no fix that does not downgrade the linter below the framework version. Reported each run, accepted as F20 |
| The framework's 404 page is static, so its scripts are blocked | It renders without JavaScript; only client-side navigation from it is lost |
| Sign-in enumeration | Accepted: Google authenticates, the refusal is generic, the signal is low value |
| Audit rows retain staff emails | A client decision on retention and lawful basis, documented in `docs/PRIVACY.md` §6, not decided here |
| **No penetration test, and no deployed system to test** | This is a code and design review with live database and browser verification. Stated so the gate is not read as broader than it is |
| Erasure cannot reach backups or bucket objects | Both named in the erasure record; the runbook has the commands |
| Fourteen phases remain uncommitted | Recommended before C15 |
