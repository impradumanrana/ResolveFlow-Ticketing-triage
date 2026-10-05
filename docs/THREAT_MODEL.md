# Threat Model

**Audience:** the client's security reviewer, and the delivery team.

**Scope:** the client-dedicated ResolveFlow deployment as it exists at C13 — a
Next.js web tier, a private FastAPI service, PostgreSQL, Google Workspace as
the mail source, and one AI provider reached with the client's own key. No
cloud resource has been created and no mailbox has been connected, so this
describes the design and the controls in code, not an operating system.

**Method:** trust boundaries first, then what an attacker at each boundary can
reach, then what stops them. Findings are tracked with severities in
[SECURITY_REVIEW.md](SECURITY_REVIEW.md).

---

## 1. What is worth protecting

| Asset | Why it matters | Where it lives |
|---|---|---|
| Customers' mail | Personal data the client is accountable for, and the reason the product exists | `messages`, `threads`, `attachments` |
| The mailbox refresh token | Grants read access to a support inbox until revoked | `mailbox_credentials`, sealed (AES-256-GCM) |
| The AI provider key | Spends the client's money | Secret Manager; never in the database |
| The decision record | Who approved what, and why. The client's defence if an answer is disputed | `actions`, `audit_events`, `draft_revisions` |
| The staff identity graph | Who may see which mailbox | `users`, `memberships`, `mailbox_permissions` |
| Availability | Support work stops if the workspace does | all of it |

## 2. Who we are defending against

| Actor | Capability assumed | Not assumed |
|---|---|---|
| **An outsider emailing the support inbox** | Can send arbitrary text and attachments into the system, repeatedly | No credentials, no network position |
| **A customer exercising their rights** | Can ask for their data, or its deletion | Not hostile, but must not be able to reach another person's data |
| **A curious or disgruntled staff member** | A valid session in one role | Cannot change their own role; cannot reach the database directly |
| **A compromised staff account** | Everything that role can do, at machine speed | — |
| **An attacker with the web tier's network position** | Can call the private API if they reach the VPC | No internal service token |
| **A compromised dependency** | Runs code in the build or the service | — |
| **The AI provider** | Sees every prompt sent to it; can return anything | Cannot reach the database |
| **The founder/operator (this project)** | Can deploy and read logs | Explicitly *not* trusted with client data: C-D012 forbids client data reaching the founder SaaS, and nothing in the product exports it |

Out of scope by decision: a hostile Google Workspace administrator (they own
the mailbox already), physical access to Google's or GCP's infrastructure, and
a hostile client owner (they own the workspace).

## 3. Trust boundaries

```
     ┌─────────────────────────────────────────────────────────────┐
     │ Internet                                                    │
     │   browser ──① HTTPS──▶ Next.js web tier (public)            │
     │   mail  ──────────────▶ Gmail  ──② OAuth/watch──▶ ingestion │
     └─────────────────────────────────────────────────────────────┘
                                 │ ③ internal token, VPC-only
                                 ▼
                    ┌────────────────────────────┐
                    │ FastAPI private service     │
                    └────────────────────────────┘
                       │ ④ IAM auth        │ ⑤ provider key
                       ▼                   ▼
                  PostgreSQL          AI provider
```

| # | Boundary | What crosses it | Control |
|---|---|---|---|
| ① | Browser → web tier | A session cookie and form data | Auth.js database sessions; `HttpOnly`, `SameSite=Lax`, `Secure`; every page and action re-derives identity and re-checks permission server-side (C03); strict CSP with a per-request nonce |
| ② | Gmail → ingestion | Attacker-authored message content | Least-privilege scopes; content normalised and bounded; attachments allowlisted by type and size and never fetched; all content fenced before it reaches a model |
| ③ | Web tier → private API | Server-derived identity headers | A shared internal token compared in constant time, plus VPC-only reachability; malformed or unknown context is refused, never defaulted |
| ④ | Service → PostgreSQL | Queries | IAM authentication over a unix socket, no password; every query scoped by `organization_id` |
| ⑤ | Service → AI provider | Prompts containing customer text | Approved models and region only; worst-case cost reserved before the call; key read per call from Secret Manager and never stored elsewhere |

## 4. Threats, by boundary

### ① The browser

| Threat | Control | Residual |
|---|---|---|
| Cross-site scripting | React escapes by default; no `dangerouslySetInnerHTML` anywhere; `default-src 'none'` with a per-request nonce and `strict-dynamic` | `strict-dynamic` trusts scripts a *running* script creates, so CSP limits injected **markup**, not an attacker who already executes JavaScript |
| Clickjacking | `frame-ancestors 'none'` and `X-Frame-Options: DENY` | — |
| Cross-site request forgery | Next server actions are origin-checked by the framework; `SameSite=Lax`; mutating routes require the session | — |
| Session theft | `HttpOnly` so script cannot read it; `Secure` outside localhost; database-backed sessions so revocation is immediate | A stolen cookie works until revoked or expired |
| Privilege escalation through the UI | Hidden buttons prove nothing: every action calls `requireAccess` server-side, and the role×permission matrix is asserted negatively for every pair (C03) | — |
| TLS downgrade | HSTS, two years, `includeSubDomains`, `preload`; `upgrade-insecure-requests` | Requires the first visit to be over HTTPS |
| Open redirect | The only externally supplied redirect is Gmail's consent URL, checked to be exactly `accounts.google.com` over HTTPS | — |
| Brute force on sign-in | Google performs authentication; the product never sees a password | Enumeration of who has access is bounded by a generic refusal page; **accepted**, see SECURITY_REVIEW F11 |

### ② Mail, the untrusted input channel

| Threat | Control | Residual |
|---|---|---|
| **Prompt injection** — a customer instructs the model | Untrusted text is fenced with a per-call random token it cannot guess, every prompt states the rule, and the output is validated against a closed enum or against its cited evidence. The worst achievable outcome is a different *allowed* classification | A mis-classification can still be induced. It cannot create a new capability, a new recipient, or a send |
| Knowledge-base poisoning | Articles are uploaded by a Knowledge Manager, not by customers; article content is fenced in the same way; answers must quote their evidence verbatim | A hostile uploader is a hostile insider, not an outsider |
| Header injection | Inbound and outbound headers are sanitised; a newline in a subject is rejected (C07, C12) | — |
| Malicious attachment | Type allowlist, size cap, filename reduced to a safe basename, bytes never fetched, and a scan gate where only `CLEAN` is readable — `PENDING` is not | The scanner itself is not implemented; the gate therefore refuses everything, which is the safe direction |
| Mail-bomb / flood | Ingestion is bounded per sync; the API carries a per-organization ceiling | No per-sender throttle at the mailbox; **accepted**, Gmail and Workspace policy sit in front |
| Spoofed sender | Addresses are recorded as received, never trusted for authorization | A spoofed address can mislead a human reviewer, as it would in any mail client |

### ③ The private API

| Threat | Control | Residual |
|---|---|---|
| Direct call from outside | VPC-only ingress (C02) plus a required internal token | A VPC foothold plus the token would reach it |
| Token comparison timing | `secrets.compare_digest` | — |
| Forged identity headers | The web tier builds them from its own session context and forwards nothing from the browser; the service refuses malformed or unknown roles | The service trusts the web tier, by design, and says so |
| Tenant crossing | Every query scoped by organization; a rendered-schema test asserts every tenant table carries the column | — |
| Denial of service by body size | 256 KiB cap, enforced before parsing, counting what arrives rather than what was declared | — |
| Denial of service by request rate | Per-policy limits on every mutating route, counted atomically, failing closed | A caller inside the VPC with the token can still consume its allowance |
| Error-message leakage | Validation failures return only the field location and the kind of problem; unhandled failures return a code and a correlation id, and what is logged is redacted | — |

### ④ PostgreSQL

| Threat | Control | Residual |
|---|---|---|
| SQL injection | Every statement is parameterised; the only interpolated values are table names from fixed module maps, asserted by tests | — |
| Credential theft from the database | Refresh tokens are sealed with AES-256-GCM under a Secret Manager key and bound to their organization and mailbox; a row copied elsewhere fails to decrypt. Access tokens are never stored | Holding both the database and the keyring defeats this |
| Audit tampering | `audit_events` and `erasure_records` refuse UPDATE and DELETE by trigger | A superuser can drop the trigger |
| Silent data loss | Retention sweeps are policy-driven, dry-run-first, and blocked by legal holds; tickets and audit rows are not sweepable at all | — |

### ⑤ The AI provider

| Threat | Control | Residual |
|---|---|---|
| Customer data leaving the client's control | Provider, models, region and budget are approved per organization before any call; data residency is passed explicitly | The provider sees the prompt. That is inherent, and is the client's decision to make |
| A hostile or wrong model response | Output is schema-validated; answers must cite evidence and quote it verbatim; ungrounded answers cannot be approved as the model's words | — |
| Runaway spend | Worst-case cost reserved atomically before the call against a monthly budget | A reservation whose worker dies holds budget until swept |
| Key exposure | Read per call from Secret Manager, held in an unprintable wrapper, never logged, never in the database | — |

### Build and supply chain

| Threat | Control | Residual |
|---|---|---|
| Vulnerable dependency | `pip-audit` and `npm audit` in CI; deployed packages block the build, build-time packages are reported | Build-time ESLint advisories are accepted and listed in SECURITY_REVIEW |
| Committed secret | `gitleaks` in CI with a file-scoped allowlist; `.env` ignored; no secret value in Terraform | — |
| Unreviewed code reaching production | CI deploys; Terraform has no apply credentials in CI; image tags are immutable | — |

## 5. What the design refuses to do

These are not controls on a feature; they are the absence of the feature, and
that is why they hold.

- **Nothing sends mail.** No send endpoint appears anywhere in the tree, a test
  asserts it per file, `organizations.sending_enabled` is constrained to false,
  and `provider_drafts` has no column that could record a send.
- **No domain-wide delegation.** The product authorizes one mailbox at a time.
- **No client data leaves for the founder's product.** There is no export path.
- **No ticket is deleted by a schedule.** Deletion is a privacy request with a
  named requester and a record.

## 6. Assumptions this model rests on

1. Google Workspace authenticates staff correctly, and the client manages that.
2. GCP's IAM, Secret Manager and Cloud SQL isolation hold.
3. The client's Workspace administrator is trusted with their own mailboxes.
4. The deployment is single-tenant, and tenant scoping is defence in depth
   rather than the only barrier.
5. Nobody has shell access to the production container. If they do, every
   in-process control above is reduced to the database's constraints.

Any of these failing changes the conclusions. They are stated so a reviewer can
disagree with them.
