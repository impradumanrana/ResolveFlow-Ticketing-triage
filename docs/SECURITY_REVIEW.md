# Security Review — C13

**Audience:** the client's security reviewer, and the delivery team.

**Date:** 2026-10-05. **Scope:** the whole client-dedicated deployment as built
through C12, reviewed against [THREAT_MODEL.md](THREAT_MODEL.md).

**Method:** the codebase was read boundary by boundary rather than scanned.
Every finding below was confirmed against the actual code or a live PostgreSQL
instance before being written down, and every fix has a test that fails if the
fix is removed — verified by injecting the removal (the mutation results in
`CLIENT_C13_TEST_REPORT.md`).

**Gate:** no open critical or high finding. **Met:** every critical and high
finding is closed. Three findings are accepted and recorded, all low or
build-time only, each with the reason and what would change the decision.

---

## Summary

| Severity | Found | Fixed | Accepted |
|---|---|---|---|
| Critical | 1 | 1 | 0 |
| High | 6 | 6 | 0 |
| Medium | 7 | 7 | 0 |
| Low | 5 | 2 | 3 |
| **Total** | **19** | **16** | **3** |

---

## Critical

### F1 — Remote code execution in the shipped Next.js version *(fixed)*

`next@16.3.4` is affected by a remote-code-execution advisory in
`next/og`'s `ImageResponse`. The application does not use `next/og`, so there
was no reachable path, but the vulnerable code shipped in the image and the fix
was a patch release.

**Fixed:** upgraded to `next@16.3.8`, pinned exactly. Production dependencies
now report zero advisories (`npm audit --omit=dev --audit-level=high`). Web
build, lint, typecheck and all 198 web tests pass on the new version.

---

## High

### F2 — No Content-Security-Policy and no HSTS *(fixed)*

The web tier set `X-Frame-Options`, `nosniff`, a referrer policy and a
permissions policy, but no CSP and no `Strict-Transport-Security`. An
injection would have had no second line of defence, and a first request over
HTTP could be downgraded indefinitely.

**Fixed:** a strict policy — `default-src 'none'`, a per-request nonce with
`strict-dynamic`, `frame-ancestors 'none'`, `base-uri 'none'`,
`object-src 'none'` — plus HSTS for two years with `includeSubDomains` and
`preload`, sent only over HTTPS. The application loads no external script,
font, image or iframe and uses no inline `style` attribute, so no `unsafe-*`
allowance was needed.

**Verified in a browser**, not only as a header: the authenticated workspace
and the sign-in page render with zero console errors, and as negative controls
a cross-origin `fetch` and a cross-origin image were both blocked with
`securitypolicyviolation` events, and a parser-inserted inline script — the
XSS vector that matters — did not execute.

**Residual, stated plainly:** `strict-dynamic` deliberately trusts scripts
created by an already-running script. The policy therefore constrains injected
*markup*, which is the common vector; it does not contain an attacker who is
already executing JavaScript. Removing `strict-dynamic` would break the
framework's own chunk loading.

**Consequence accepted:** pages carrying a nonce cannot be statically
prerendered, so `/`, `/signin` and `/access-denied` are now rendered per
request. See F18 for the one page that stays static.

### F3 — No rate limiting anywhere *(fixed)*

No endpoint, server action or sign-in path bounded how often it could be
called. A compromised account, or a loop in the web tier, could drive
unbounded database writes and unbounded provider spend.

**Fixed:** seven named policies, each with a stated reason, enforced on every
mutating route. Counting is a single atomic upsert into `rate_limit_counters`,
so the count is correct under concurrency; keys are stored as SHA-256 so the
table holds no identifier; windows are clock-aligned so instances agree without
coordinating; and the limiter **fails closed** — which costs nothing, because
every limited action already needs the same database.

Mutations happen in two tiers, so the limiter exists twice over one table and
one policy list, with a test asserting the two copies agree.

**Verified live:** 40 concurrent attempts against a 10-per-minute policy
granted exactly 10, refused 30, and left one row counting all 40.

### F4 — No request body size limit *(fixed)*

The private API would parse a body of any size. A single request could make the
service allocate as much memory as the caller cared to send.

**Fixed:** 256 KiB, refused *before* parsing, and counting what actually
arrives rather than trusting `Content-Length` — so a request that lies about
its length is refused too. The largest legitimate payload (a 20,000-character
draft body) has an order of magnitude of headroom.

### F5 — The attachment scan gate was promised but did not exist *(fixed)*

The C04 schema declared "nothing may read one until the scan state says it is
clean" and deferred the scanner to C13. Nothing enforced it, and nothing set
`scan_state` to anything other than `PENDING` or `SKIPPED`.

**Fixed:** one gate, `require_readable`, where exactly one state passes.
`PENDING` does not — "not yet known to be bad" is not "known to be good", so a
verdict that never arrives fails closed. An unconfigured deployment gets a
scanner that returns `FAILED`, never `CLEAN`. A verdict cannot overwrite an
earlier `INFECTED`, and a `CLEAN` verdict must name the bytes it judged.

**Residual:** no scanner is integrated, so in practice nothing is readable.
That is the safe direction and it costs nothing today, because **no code path
fetches attachment bytes at all** — asserted by a scan with a positive control,
and the web tier offers no download link or route. The gate is written first so
that the day a download is added, the test fails until it routes through it.

### F6 — No data-subject export or erasure *(fixed)*

C04 deliberately excluded tickets from the retention sweep and recorded that
deletion belongs to a privacy workflow in C13. That workflow did not exist, so
the deployment could not satisfy an access or erasure request.

**Fixed:** `app/privacy.py` provides both. Export gives the person their own
data unredacted while redacting *other* participants — answering one access
request must not create a breach for someone else. Erasure keeps every row and
destroys the content across all eight tables that hold it, replaces addresses
with a reserved `.invalid` address, recomputes the draft checksum rather than
leaving it pointing at vanished text, and records what it did in an append-only
`erasure_records` row identified by a digest rather than the address.

Erasure refuses while a legal hold is active, refuses any role but Owner,
refuses across organizations, and is idempotent.

**Verified live: 62 of 62 checks**, including the negative controls.

### F7 — Known vulnerabilities in Python dependencies *(fixed)*

24 advisories across `PyJWT 2.13.0` (13), `pypdf 6.16.2` (8) and
`urllib3 2.7.0` (3).

**Fixed:** upgraded to `PyJWT 2.15.0`, `pypdf 6.19.0`, `urllib3 2.8.0`.
`pip-audit` now reports no known vulnerabilities, `pip check` reports no broken
requirements, and all 1,816 Python tests pass on the new versions.

---

## Medium

### F8 — Customer text reached the classifier with no instruction boundary *(fixed)*

The grounded-drafting prompt told the model that the ticket was untrusted data.
The **classification** prompt did not: it passed the raw ticket body as a user
message with no such statement. An emailed instruction could therefore try to
influence category, urgency and route.

**Fixed:** every prompt that embeds untrusted content now fences it with a
per-call random token the content cannot guess — any forged delimiter in the
content is stripped before wrapping — and states the rule. An AST test holds
this for every prompt site in the codebase, including the dict-literal form
the SDK transport uses, so a new call site cannot skip it.

**Residual:** a sufficiently clever message may still shift a classification.
What bounds the damage is not the prompt but the closed output: classification
is validated against a fixed enum, drafting must quote its evidence verbatim,
and the route is then re-decided by deterministic rules that can only narrow it.
An injection cannot create a capability, a recipient, or a send.

### F9 — No redaction for logs, traces or audit metadata *(fixed)*

A ticket body or a credential quoted into an exception message would reach a
log aggregator, whose audience and retention differ from the data's.

**Fixed:** one redaction module, used by the API's unhandled-failure handler.
It removes provider credentials, emails, IBANs, phone numbers and
Luhn-valid card numbers, and redacts by mapping key as well as by value, so a
value under `refresh_token_envelope` is removed whatever it looks like. It
deliberately preserves what an operator needs — UUIDs, references, timestamps,
status codes, scope URLs, secret *names* — because unusable logs get switched
off, and a long number that fails Luhn is left alone for the same reason.

### F10 — Audit metadata could outlive an erasure *(resolved by design)*

`audit_events` refuses UPDATE and DELETE by trigger, so anything written there
survives an erasure request and cannot be removed afterwards.

**Resolved by making the premise true rather than by weakening the trigger:**
audit rows carry staff actors, operational codes, identifiers and counts — never
customer content. An AST test asserts that no audit writer passes a content
field (`body`, `subject`, `snippet`, `customer_address`, …), so the
incompatibility cannot be introduced later. The erasure workflow does not touch
the audit trail, and a test asserts that too.

**Residual, and a matter for the client's legal basis rather than code:** audit
rows retain the *staff* member's email. Retention of a decision record is a
legitimate-interest and legal-obligation question, and is documented in
[PRIVACY.md](PRIVACY.md) rather than decided here.

### F11 — CI was four phases behind the Makefile *(fixed)*

The CI job carried its own inline copy of the lint and type-check path lists.
The Makefile's lists advanced through C09–C12; CI's did not. For four phases
the rules engine, the AI gateway, the triage pipeline and the review service
were unchecked in CI while every local run passed.

**Fixed:** CI calls `make python-check`. The Makefile states its paths once, in
variables used by every target, and `tests` is linted whole so a new test file
cannot be unlinted. Four tests assert all of this, naming the defect they
prevent.

### F12 — No dependency or secret scanning *(fixed)*

**Fixed:** a separate CI job runs `pip-audit` (blocking), `npm audit
--omit=dev` (blocking — those packages are what ship), `npm audit` over
build-time packages (reported, not blocking) and `gitleaks` with a
**file-scoped** allowlist. The allowlist names files where simulated
credentials are deliberately present; it allowlists no pattern, because an
allowlisted pattern would hide a real secret anywhere it appeared.

### F13 — A rate-limit constraint compared two different clocks *(fixed)*

The first version of the counter table constrained `window_start <= updated_at`.
`window_start` comes from the application's clock and `updated_at` from the
database's. A few seconds of skew would make the insert fail at a window
boundary, and because the limiter fails closed, the request would be refused.

**Found by the live run, not by review.** Removed, with the reasoning recorded
in the migration so it is not reintroduced. Every remaining constraint on that
table concerns the row's own integrity and needs no second clock.

### F14 — The erasure tombstone violated a schema constraint *(fixed)*

`triage_runs.trace` is constrained to a JSON array (C11). The erasure wrote an
object, so erasing a conversation that had been triaged would have failed
mid-transaction.

**Found by the live run.** Fixed to `[{"erased": true}]` — an array, and one
that says the trace was erased rather than never recorded. A test asserts the
tombstone parses as an array.

---

## Low

### F15 — Unhandled failures had no correlation id and uncontrolled tracebacks *(fixed)*

**Fixed:** an application-wide handler returns `{schema_version, ok, code}` with
`INTERNAL_ERROR` and nothing about the failure, and logs one line carrying the
request id the web tier already sends, bounded in length, with the exception
chain redacted.

### F16 — Disconnecting a mailbox was not rate limited *(fixed)*

Found by the test written for F3, which asserts that every mutating route names
a policy. `DELETE /v1/mailboxes/{id}/connection` named none.

**Fixed:** connect and disconnect share one allowance — it is churn on a single
mailbox that is worth bounding, in either direction.

### F17 — Address validation accepted a missing local part *(fixed)*

`@example.net` passed, so an export or erasure could be requested for an
address that cannot exist. Found by a parametrized test.

**Fixed:** deliberately permissive but not credulous — a local part, an `@`,
and a domain containing a dot.

### F18 — The framework's 404 page is static, so its scripts carry no nonce *(accepted)*

`/_not-found` is prerendered by Next, so the nonce cannot be baked in, and
`strict-dynamic` makes `'self'` inoperative. Its scripts are therefore blocked.

**Accepted:** the page is static HTML that renders and reads correctly without
JavaScript; only client-side navigation from it is lost. Forcing it dynamic
would mean replacing the framework's error page to gain nothing a user notices.
**What would change this:** the 404 page acquiring interactive content.

### F19 — Sign-in does not enumerate-protect *(accepted)*

Someone with a Google account can learn whether that account has access by
attempting to sign in and seeing the refusal.

**Accepted:** Google performs authentication, so there is no password to brute
force; the refusal page is generic and names no reason; and the signal
("this address has access to some workspace") is low value against the cost of
rate-limiting a flow Google already protects. **What would change this:** a
self-service sign-up flow, or a client requirement to hide membership.

### F20 — Build-time dependencies carry denial-of-service advisories *(accepted)*

Five high advisories remain in the `eslint-config-next` → `@next/eslint-plugin-next`
→ `fast-glob` → `micromatch` → `braces` chain.

**Accepted:** none of it reaches the image — `output: standalone` bundles
production dependencies only — and the sole published fix downgrades
`eslint-config-next` to 14.2.35, below the framework version in use. The
blocking CI audit is scoped to deployed packages; a second, non-blocking step
keeps this list visible so it is reviewed each phase rather than forgotten.
**What would change this:** a compatible fix being published, or any of these
packages entering `dependencies`.

---

## What this review did not cover

Stated so the gate is not read as broader than it is.

- **No penetration test.** This is a code and design review with live
  verification against PostgreSQL and a browser. No deployed system exists to
  test, and no external assessment has been commissioned.
- **No cloud configuration review in practice.** The GCP design is verified as
  code (63 files, 13 modules) and has never been applied, so there is no
  running IAM policy, firewall or service account to inspect.
- **No live provider behaviour.** No paid model has been called, so the
  prompt-injection resistance of the chosen model is untested. The structural
  defences are what the gate rests on.
- **No mailbox has been connected**, so the ingestion boundary is exercised
  only against a simulated Google.
- **Availability and load** are reasoned about, not measured.
