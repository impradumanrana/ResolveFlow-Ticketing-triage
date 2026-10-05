# Handover: Client-Owned Administration

**Audience:** the client's administrator and sponsor taking ownership of the
running system.

**Status:** this is the handover *plan*. Nothing has been handed over, because
nothing is deployed. Every signature block is empty and every "verified" column
is blank. It is written now so that the handover is a checklist on the day
rather than a conversation.

---

## 1. What the client ends up owning

The intent of this delivery is that the client owns everything, and the
delivery side can be removed without the system stopping.

| Thing | Owned by the client from day one | Note |
|---|---|---|
| The GCP project, billing, and every resource in it | **Yes** | Created in their project, under their billing account |
| The Gmail OAuth client and its secret | **Yes** | Created by their Workspace administrator, in their project |
| The AI provider account and key | **Yes** | Their account, their spend, their region choice |
| The database and everything in it | **Yes** | Their customers' data, in their project |
| The domain and DNS | **Yes** | — |
| The Owner role in the workspace | **Yes** | Seeded to a named person at the client |
| The source code | Per the engagement agreement | Not decided in this document |

**What the delivery side holds after handover:** whatever access the client
chooses to grant for support, and nothing by default. There is no standing
access, no telemetry, and no path by which data from this deployment reaches
anything else (C-D012).

## 2. Before handover

Each row needs a date and an initial. An unticked row is not a handover.

### Deployment and recovery

| # | Item | How to verify | Verified |
|---|---|---|---|
| 1.1 | Production deployed through CI, not from a laptop | The release has a CI run id and an immutable image tag | |
| 1.2 | Smoke tests pass against production | `make smoke API_URL=… WEB_URL=… DATABASE_URL=…` | |
| 1.3 | A restore has been performed on the real instance, not just drilled | Clone to a recovery instance, check the revision and the append-only triggers (`infra/docs/RUNBOOK.md` → Database restore) | |
| 1.4 | A rollback has been performed once, deliberately | Deploy the previous image tag and confirm the service serves | |
| 1.5 | Backups enabled, with point-in-time recovery and a stated retention | Cloud SQL console, and the Terraform that set it | |
| 1.6 | The next restore drill is scheduled | Quarterly, per `docs/SUPPORT.md` §6 | |

### Monitoring and cost

| # | Item | How to verify | Verified |
|---|---|---|---|
| 2.1 | All five alert policies exist and notify a real address | Trigger one deliberately and confirm somebody receives it | |
| 2.2 | Someone is named as the recipient, and knows they are | `docs/SUPPORT.md` §1, filled in | |
| 2.3 | A GCP budget with alert thresholds is set | Billing console | |
| 2.4 | The AI monthly budget is set in the product, and understood to be a hard stop | AI settings page; a zero budget refuses every call | |
| 2.5 | A week of real spend has been reviewed against the budget | The budget ledger | |

### Access and audit

| # | Item | How to verify | Verified |
|---|---|---|---|
| 3.1 | The Owner is a named person at the client, seeded securely | `scripts/bootstrap_organization.py`; no shared password at any point | |
| 3.2 | Delivery-side accounts are removed or reduced to what support needs | `make access-review` shows exactly who remains | |
| 3.3 | An access review has been run and is clean | `make access-review` exits zero | |
| 3.4 | The sign-in domain allowlist is the client's domains only | The review reports them | |
| 3.5 | The audit trail is append-only and has been read once, by the client | Try an `UPDATE` on `audit_events`; it must be refused | |
| 3.6 | The monthly access review is in somebody's calendar | — | |

### Privacy

| # | Item | How to verify | Verified |
|---|---|---|---|
| 4.1 | Retention periods are set per data class by the Owner | The retention page | |
| 4.2 | A retention sweep has been run as a dry run and the plan read | `python -m app.retention --dry-run` | |
| 4.3 | A data-subject export has been run once, on a test customer | `infra/docs/RUNBOOK.md` → Answering a data-subject request | |
| 4.4 | An erasure has been run once, and the result inspected | Same; the conversation keeps its history and loses its content | |
| 4.5 | The client's position on audit retention for staff is recorded | `docs/PRIVACY.md` §6 | |
| 4.6 | Whoever answers a subject request knows where the procedure is | — | |

### Mail and the pilot

| # | Item | How to verify | Verified |
|---|---|---|---|
| 5.1 | The production mailbox is connected, by the client's own administrator | Mailbox shows `CONNECTED` | |
| 5.2 | The pilot stage is understood, and which one production is in | `GET /v1/capabilities` reports it | |
| 5.3 | If in Draft stage, the six Observe Mode exit criteria were met and recorded | `docs/UAT_CRITERIA.md` §6 | |
| 5.4 | Reviewers know the product never sends | Say it out loud in training; it is the thing people most often assume otherwise | |

## 3. Training

Four short sessions, by role. Each ends with the person doing the thing, not
watching it.

| Session | For | Covers |
|---|---|---|
| **Reviewing** (45 min) | Agents, Supervisors | Opening a conversation, reading a recommendation and its citations, approving, editing before approving, rejecting with a reason, what a version conflict means, and why an ungrounded answer cannot be approved as the model's words |
| **Running the workspace** (45 min) | Admins | Inviting people and choosing roles, mailbox permissions, connecting and disconnecting a mailbox, AI settings and the budget, reading the audit trail |
| **Owning it** (30 min) | Owner, sponsor | Retention, legal holds, data-subject requests, the access review, what the budget does when it runs out |
| **When it breaks** (30 min) | Admins, support lead | The severities, what to report and to whom, the containment steps they can take themselves, and the three they should not |

Two things worth saying in every session, because they are the two most common
wrong assumptions:

1. **Nothing is sent to a customer by the product.** An approval produces a
   draft that a person opens and sends. There is no setting that changes this.
2. **A hidden button is not a permission.** Every action is re-checked on the
   server, so trying something that is not yours simply fails — safely.

## 4. Rollback and support boundaries

Recorded here because "we assumed you would handle that" is the most expensive
sentence in a handover.

**Rollback.** The delivery side can roll back a release — previous image tag,
or a database restore — for as long as the support arrangement runs. After
that, the procedures are in `infra/docs/RUNBOOK.md` and the client can run
them; they need the GCP permissions, not us.

**Support.** Scope, severities and response targets are in `docs/SUPPORT.md`.
In short: the software is ours; Google Workspace, the AI provider's
availability and pricing, the content of the knowledge base, and answering
customers are the client's. There is **no 24/7 rota** — this is a
single-engineer delivery, and round-the-clock cover is a different commercial
arrangement.

**What ends when support ends:** our monitoring of their alerts, our running of
their procedures, and any access they granted us. What does not end: the
runbook, the incident response plan, the smoke tests and the access review,
all of which run without us.

## 5. Known limitations, handed over knowingly

Repeated here so they are not discovered later. Full detail in the phase
reports.

- **No attachment is read.** Metadata only. A conversation whose substance is
  in a PDF will be escalated to a person, by design.
- **No attachment scanner is integrated.** The gate refuses everything, which
  is the safe direction, and nothing fetches attachment bytes.
- **Prompt injection can shift a classification.** It cannot create a
  capability, a recipient, or a message to a customer.
- **The knowledge base is the ceiling on answer quality.** Thin content shows
  up as escalations, not wrong answers.
- **Build-time dependency advisories remain** (five, ESLint tooling), none of
  which reaches the deployed image. `docs/SECURITY_REVIEW.md` F20.
- **Erasure cannot reach backups or the attachment bucket.** Both are named in
  the erasure record; the bucket step is manual.
- **No penetration test has been performed**, and no external security
  assessment has been commissioned.

## 6. Sign-off

Handover is complete when every row in §2 has a date, the four training
sessions have happened, and both parties sign.

| Role | Name | Date | Signature |
|---|---|---|---|
| Client sponsor | | | |
| Client administrator | | | |
| Data-protection lead | | | |
| Delivery (ResolveFlow) | | | |

**Items explicitly carried after handover**, each with an owner and a date, or
they are not carried — they are forgotten:

| # | Item | Owner | Due |
|---|---|---|---|
| | | | |
