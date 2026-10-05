# Production Go / No-Go

**Audience:** the client's sponsor, making the launch decision; the delivery
side preparing it.

**Current recommendation: NO-GO.** Not because anything is known to be wrong,
but because the pilot that would tell us has not run. Eleven of the twenty-two
items below cannot be assessed without a staging deployment, a connected
mailbox and an approved model — none of which exist, all of which need the
client's authorization.

This document is a checklist, not a formality. Every row is either evidenced or
blocked, and a blocked row names who unblocks it.

---

## How to read the status column

| Status | Means |
|---|---|
| **Ready** | Done and evidenced. The evidence column says where to check. |
| **Blocked** | Cannot be done by delivery. Needs a client decision, client input, or a client action. |
| **Pending pilot** | Can only be assessed by running the pilot against real mail. |

---

## 1. The software

| # | Item | Status | Evidence |
|---|---|---|---|
| 1.1 | All automated checks pass | **Ready** | `make check` — Python and web suites, lint, types, migration chain, infrastructure checks |
| 1.2 | No open critical or high security finding | **Ready** | `docs/SECURITY_REVIEW.md` — 19 findings, 16 fixed, 3 accepted (all low or build-time only) |
| 1.3 | Dependencies carry no known advisory in deployed packages | **Ready** | `make security-check`; the build-time ESLint chain is accepted and listed as F20 |
| 1.4 | A threat model exists and names its assumptions | **Ready** | `docs/THREAT_MODEL.md` |
| 1.5 | Nothing can send mail | **Ready** | No send endpoint in the tree (asserted per file), `sending_enabled` constrained false, no column could record a send |
| 1.6 | Restore and rollback are drilled, not assumed | **Ready** | C13: 23/23 live checks, including that constraints and triggers still enforce after a restore |
| 1.7 | Privacy export and erasure work | **Ready** | C13: 62/62 live checks, including legal-hold and cross-tenant refusals |
| 1.8 | Rate limits and request limits hold under concurrency | **Ready** | C13: 40 concurrent attempts granted exactly 10 |

## 2. The deployment

| # | Item | Status | Who unblocks |
|---|---|---|---|
| 2.1 | Infrastructure defined as code and verified offline | **Ready** | — (63 files, 13 modules; `scripts/validate_infra.py`) |
| 2.2 | Staging deployed and reachable | **Blocked** | Client sponsor: authorize the deploy and provide the GCP project |
| 2.3 | Production deployed through reviewed CI, not from a laptop | **Blocked** | Follows 2.2 |
| 2.4 | Backups configured, with a tested point-in-time restore on the real instance | **Blocked** | Follows 2.2. The procedure is drilled (1.6); the instance does not exist |
| 2.5 | Alerts firing to a real destination, with someone receiving them | **Blocked** | Client: alert email addresses |
| 2.6 | Budgets and budget alerts set in GCP | **Blocked** | Client: billing account |
| 2.7 | Secrets present in Secret Manager, none in source or Terraform | **Blocked** | Client: the OAuth client secret and the provider key. Verified absent from source already |
| 2.8 | A domain mapped, with HTTPS and HSTS confirmed on the real host | **Blocked** | Client: the hostname |

## 3. Identity and access

| # | Item | Status | Who unblocks |
|---|---|---|---|
| 3.1 | Role and permission model implemented and negatively tested | **Ready** | C03; every role×permission pair asserted, including refusals |
| 3.2 | The Owner seeded securely, not through a shared password | **Blocked** | Client: who the Owner is. Procedure exists (`scripts/bootstrap_organization.py`) |
| 3.3 | Real people invited with the roles they should have | **Blocked** | Client support lead |
| 3.4 | An access review done, and a date set for the next | **Blocked** | Client Admin; monthly per `docs/SUPPORT.md` |
| 3.5 | Allowed sign-in domains restricted to the client's | **Blocked** | Client: their domain(s) |

## 4. Mail and knowledge

| # | Item | Status | Who unblocks |
|---|---|---|---|
| 4.1 | Mailbox connection built, with least-privilege scopes | **Ready** | C06; C12a added optional `gmail.compose` |
| 4.2 | A real mailbox connected and reading | **Blocked** | Workspace admin: create the OAuth client, consent as the mailbox |
| 4.3 | Approved knowledge content ingested | **Blocked** | Client support lead: the content, and who may upload it |
| 4.4 | Retrieval quality measured against that corpus | **Pending pilot** | Follows 4.3 |

## 5. The AI

| # | Item | Status | Who unblocks |
|---|---|---|---|
| 5.1 | One gateway for every model call, refusing anything unapproved | **Ready** | C10 |
| 5.2 | Provider, models, region and budget approved | **Blocked** | Client sponsor + data-protection lead. **No model has ever been called** |
| 5.3 | Accuracy measured against the client's labelled cases | **Pending pilot** | Needs 5.2 and UAT precondition 6 |
| 5.4 | Cost per conversation measured and within budget | **Pending pilot** | Needs 5.2 |
| 5.5 | Latency measured against a real provider | **Pending pilot** | Needs 5.2 |
| 5.6 | The AI report completed from live results | **Pending pilot** | `docs/AI_PERFORMANCE_REPORT.md` — pre-pilot sections are filled; live sections are not |

## 6. Acceptance

| # | Item | Status | Who unblocks |
|---|---|---|---|
| 6.1 | UAT criteria written | **Ready** | `docs/UAT_CRITERIA.md` |
| 6.2 | UAT criteria **signed before** the pilot | **Blocked** | Client sponsor, support lead, data-protection lead |
| 6.3 | Role walkthroughs completed by the client's own people | **Pending pilot** | `docs/UAT_CRITERIA.md` §4 |
| 6.4 | Observe Mode run for at least two weeks on real mail | **Pending pilot** | `app/pilot.py` exit criteria |
| 6.5 | Observe Mode exit criteria met and recorded | **Pending pilot** | Six conditions, enforced as a list in code and mirrored in the UAT document |
| 6.6 | Acceptance signed **after** the pilot | **Blocked** | Follows 6.3–6.5 |

## 7. Operations and handover

| # | Item | Status | Who unblocks |
|---|---|---|---|
| 7.1 | Runbook covers deploy, rollback, restore, rotation, privacy requests | **Ready** | `infra/docs/RUNBOOK.md` |
| 7.2 | Incident response plan exists | **Ready** | `infra/docs/INCIDENT_RESPONSE.md` |
| 7.3 | Support contacts and severities **agreed and filled in** | **Blocked** | Both sides. `docs/SUPPORT.md` is deliberately blank where names go |
| 7.4 | Retention periods set per data class | **Blocked** | Client data-protection lead |
| 7.5 | The client's position on audit retention for staff recorded | **Blocked** | Client data-protection lead (see `docs/PRIVACY.md` §6) |
| 7.6 | Client-owned administration handed over, with training | **Blocked** | Scheduled with the client; C15 |
| 7.7 | Rollback and support boundaries agreed in writing | **Blocked** | Client sponsor; `docs/SUPPORT.md` §4 and §7 |

---

## Summary

| Section | Ready | Blocked | Pending pilot |
|---|---|---|---|
| 1. Software | 8 | 0 | 0 |
| 2. Deployment | 1 | 7 | 0 |
| 3. Identity | 1 | 4 | 0 |
| 4. Mail and knowledge | 1 | 2 | 1 |
| 5. AI | 1 | 1 | 4 |
| 6. Acceptance | 1 | 2 | 3 |
| 7. Operations | 2 | 5 | 0 |
| **Total** | **15** | **21** | **8** |

**Everything within delivery's control is done.** The software, its security
posture, its recovery procedures and its privacy mechanisms are built and
evidenced. What remains is almost entirely things only the client can provide
or decide, and the pilot that those unlock.

## The decision

**NO-GO**, with a clear path:

1. **Client authorizes staging** and provides the GCP project, billing, domain
   and alert destinations (§2).
2. **Client approves an AI provider**, model, region and budget (5.2). Nothing
   calls a model before this.
3. **Workspace admin creates the OAuth client** and connects a test mailbox
   (4.2).
4. **Client supplies approved content and 30+ labelled cases** (4.3, UAT
   precondition 6).
5. **Sign the UAT criteria** (6.2), then run the pilot in Observe Mode.
6. **Review this document again** with sections 4–6 completed.

Steps 1–4 are the critical path, and three of them are decisions rather than
work. Until they are made, no amount of further engineering moves the launch
date.

## Sign-off

| Role | Name | Date | Decision | Signature |
|---|---|---|---|---|
| Client sponsor | | | | |
| Support lead | | | | |
| Data-protection lead | | | | |
| Delivery (ResolveFlow) | | | | |
