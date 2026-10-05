# Privacy: What Is Held, For How Long, And How To Remove It

**Audience:** the client's data-protection lead, and whoever answers a customer
who asks about their data.

**Status:** the mechanisms described here are built and verified against a real
database (62 of 62 checks, recorded in `CLIENT_C13_TEST_REPORT.md`). No
customer data exists yet, because no mailbox has been connected.

This document describes what the system does. It is not legal advice, and it
does not decide the client's lawful basis for any of it — those are the
client's decisions, and the places where one is needed are marked.

---

## 1. Whose data, and what

Two different groups of people appear in the system, and they are treated
differently.

### Customers (data subjects)

People who email the client's support address. For each, the system holds:

| What | Where | Why it is there |
|---|---|---|
| Email address | `messages`, `threads`, `tickets` | To know who wrote, and who a reply would go to |
| Message subject and body | `messages` | The support request itself |
| Attachment names, types and sizes | `attachments` | To show what was sent. **Attachment contents are never downloaded** |
| The AI's classification and reasoning trace | `triage_runs` | To show why a conversation was routed as it was |
| A reply prepared for them | `draft_revisions` | What a person reviewed and approved |
| VIP status, if the client set it | `vip_contacts` | Client-configured priority |

### Staff (the client's own people)

| What | Where | Why |
|---|---|---|
| Name, email, Google account link | `users`, `accounts` | Sign-in |
| Role, department, mailbox permissions | `memberships`, `mailbox_permissions`, `department_memberships` | Who may see and do what |
| Every decision they made, with reason and timestamp | `actions`, `audit_events` | The client's record of who approved what |

## 2. What the system deliberately does not hold

- **Attachment contents.** Metadata only; the bytes are never fetched.
- **Passwords.** Google authenticates staff; no password reaches this system.
- **Access tokens.** Minted when needed, held in memory for at most 45
  minutes, never written down.
- **Customer content in audit rows.** Audit records carry staff actors, codes,
  identifiers and counts. This is deliberate, and it is what makes a retained
  audit trail compatible with erasure (§5).
- **Customer content in rate-limit counters.** Keys are stored as SHA-256.
- **Anything in the founder's product.** There is no export path from this
  deployment to the SaaS ResolveFlow is also building (C-D012).

## 3. Where it goes

| Recipient | What they see | Control |
|---|---|---|
| **Google** (Workspace) | Everything — it is their mailbox | The client's existing relationship |
| **The AI provider** the client chooses | The ticket text, and the knowledge excerpts used to answer it, for each triaged conversation | Provider, models, **region** and budget are approved by the client before any call. Region is passed explicitly, so a client who requires EU processing gets it |
| **Google Cloud** | Everything at rest, in the client's own project | The client's project, the client's keys |
| **Nobody else** | — | There is no analytics, telemetry, error-reporting or support-bundle egress |

**A client decision is needed here:** whether to send ticket text to a
third-party model at all, and if so in which region. Until a provider is
approved, no model is called.

## 4. Retention

Retention is per data class, set by the Owner, and swept on a schedule.

| Data class | Retention | Notes |
|---|---|---|
| Messages | client-set | The sweep deletes by age |
| Attachments | client-set | Metadata rows |
| Triage runs | client-set | The AI's working record |
| Jobs, usage, evaluations | client-set | Operational |
| **Tickets** | **not sweepable** | Deleting a ticket would orphan its conversation and its decision history. Ticket removal is an erasure request (§5), with a named requester and a record |
| **Audit events** | **not sweepable** | Removing audit history is a separate, explicitly approved procedure, not a routine sweep |
| Rate-limit counters | 2 days | Contain no personal data |

A **legal hold** stops the sweep for a data class, or for the whole
organization. While a hold is active, erasure is refused too (§5).

Every sweep is planned before it runs, can be run as a dry run, and is
recorded.

## 5. Answering a data-subject request

Both operations are performed by the Owner and are recorded. Neither is exposed
in the web interface: they are run deliberately, by an operator, which is
appropriate for an action that cannot be undone. `infra/docs/RUNBOOK.md` has
the commands.

### Access ("send me my data")

Produces a structured export containing that person's tickets, their messages
with full text, attachment metadata, and the replies prepared for them.

Three properties worth stating to a customer:

- **It is not redacted** where it concerns them. They are entitled to it.
- **Other people are redacted.** If a third party was copied on their email,
  that address is removed. Answering one person's access request must not
  disclose someone else's data.
- Records of which support agent handled the conversation are **not** included.
  That is the client's staff data and its own audit record.

### Erasure ("delete my data")

Erasure **keeps the rows and destroys the content**. This is a deliberate
choice, and the reasoning should be explained to anyone who asks:

- Every personal field across the eight tables that hold one becomes a
  tombstone, and the address becomes a reserved `.invalid` address that cannot
  route anywhere.
- The conversation is marked deleted, so the workspace stops showing it.
- What survives is the shape of the record: that a conversation existed, which
  agent acted on it, when, and with what decision. Nothing about the person.

Deleting the rows outright would take the decision history with them. The
client needs that history, and the audit trail is required to retain it.

**Erasure is refused when:** a legal hold is active, the caller is not the
Owner, the caller belongs to a different organization, or the address is
malformed. A repeat request for someone already erased is recognised and
reported rather than run again.

**Two things erasure does not do, and an operator must:**

1. **Attachment objects in storage.** The bucket is a different system. The
   erasure record names every object that must be deleted there, and the
   runbook has the command.
2. **Backups.** A database backup taken before the erasure still contains the
   data until it ages out. This is normal, and it is the usual answer given to
   a data subject, but the client should state their backup retention period
   when they answer one.

### What erasure leaves on purpose

- The **audit trail**, which holds no customer content but does hold the
  **staff** member's email and action. Retaining a record of who did what is a
  legitimate-interest and in places a legal-obligation matter. **A client
  decision is needed** on how long that is kept; this system should not decide
  it for them.
- **Counts and timestamps**, so reporting and SLA history are not falsified.

## 6. If a staff member asks for erasure

Not the same request, and not automated. A staff member's identity is tied to
their Google account and to the decision record. The usual answer is to revoke
their membership — which ends access immediately — while retaining the audit
record of what they did, under the client's legal basis for keeping it. This
needs the client's own position and is listed as an open item in the C13
report.

## 7. Where to look

| Question | File |
|---|---|
| What an attacker could reach, and what stops them | `docs/THREAT_MODEL.md` |
| Which findings were found, fixed, or accepted | `docs/SECURITY_REVIEW.md` |
| How to run an export, an erasure, or a sweep | `infra/docs/RUNBOOK.md` |
| What the mailbox connection can and cannot read | `docs/MAILBOX_CONNECTION_MODES.md` |
| What to do if data is exposed | `infra/docs/INCIDENT_RESPONSE.md` |
