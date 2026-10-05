# Support, Contacts, And Escalation

**Audience:** the client's support lead and anyone at the client who needs to
report a problem; the delivery side on call.

**Status:** the structure below is proposed and the response targets are
proposed. **Every name, address and phone number is blank and must be filled in
and agreed before production.** A support plan with placeholder contacts is not
a support plan, so the gaps are left visible rather than filled with guesses.

---

## 1. Contacts

### Client side

| Role | Name | Contact | Hours |
|---|---|---|---|
| Sponsor (commercial decisions, go/no-go) | | | |
| Support lead (day-to-day, triage quality) | | | |
| Workspace administrator (mailboxes, OAuth, Google) | | | |
| Data-protection lead (subject requests, breach) | | | |
| Out-of-hours contact | | | |

### Delivery side

| Role | Name | Contact | Hours |
|---|---|---|---|
| Primary engineer | | | |
| Escalation | | | |

**This is a single-engineer delivery.** There is no 24/7 rota and no second
line. That is a material fact about the support model, not an omission, and the
targets in §3 are set accordingly. If the client needs round-the-clock cover,
that is a different commercial arrangement and should be agreed before launch
rather than discovered during an incident.

## 2. How to report something

| What | Where | Include |
|---|---|---|
| Something is wrong with an answer or a route | Support lead, then delivery | The conversation reference (e.g. `#1042`) and what you expected |
| Someone cannot sign in or cannot see a mailbox | Client Admin first — most of these are permissions | The person's email and what they tried |
| Mail has stopped arriving | Delivery, immediately | When it last worked |
| Anything involving customer data being visible to the wrong person | Delivery **and** the data-protection lead, immediately | Nothing else — just report it; do not investigate first |

**Always include the correlation id if the screen showed one.** Every failed
request logs one line carrying it, and it is the difference between finding the
cause in minutes and guessing for an hour.

**Never include customer personal data in a report.** The conversation
reference is enough for us to find it.

## 3. Severities and response targets

Proposed, to be agreed. "Response" means a human has acknowledged and started;
it does not mean fixed.

| Severity | Means | Examples | Response target |
|---|---|---|---|
| **S1** | Customer data exposed, or the workspace is unusable | Wrong person can see a mailbox; everyone locked out; mail not being read for hours | **1 hour**, within agreed hours; immediate containment takes priority over diagnosis |
| **S2** | A core function is broken for everyone | Triage has stopped; no answers are being produced; AI spend has stopped work | **4 business hours** |
| **S3** | Degraded or wrong behaviour with a workaround | A route is consistently wrong; a page is slow; one person's permissions are off | **2 business days** |
| **S4** | Cosmetic, or a request | Wording, a report, a question | **5 business days** |

An S1 is declared by either side, not negotiated. If the client thinks it is an
S1, it is an S1 until shown otherwise.

## 4. What is in scope

**In scope**

- The software: defects, outages, data integrity, security issues.
- Operational procedures: deploys, rollbacks, restores, key rotation.
- Privacy requests: running an export or erasure on request.
- Triage quality: investigating why something was routed as it was.

**Out of scope**

- **Google Workspace itself.** Mailbox configuration, group membership,
  licences, DNS and account lockouts are the client's Workspace
  administrator's. We can say what the product needs; we cannot change their
  Google settings.
- **The AI provider's availability or pricing.** The client's account, the
  client's key, the client's relationship. We handle the failure gracefully; we
  cannot make the provider faster or cheaper.
- **Writing the client's support content.** We index approved content; a
  retrieval gap caused by missing content is a content task, not a defect.
  This is the single most common cause of "the AI could not answer".
- **Answering customers.** The product drafts; a person sends. We never send
  anything, and we never answer a customer on the client's behalf.
- **Legal advice.** Retention periods, lawful basis and breach notification
  decisions are the client's; `docs/PRIVACY.md` marks where a decision is
  needed.

## 5. Escalation path

1. **Client Admin** — permissions, mailbox connection, AI settings. Most S3s
   end here.
2. **Delivery primary** — anything the Admin cannot resolve, and every S1/S2.
3. **Delivery escalation** — unreachable primary, or an S1 lasting more than
   four hours.
4. **Client sponsor** — commercial impact, or a decision to roll back or pause
   the pilot.

For an S1 involving customer data, step 2 and the client's data-protection lead
happen **in parallel**, not in sequence. Their notification clock starts when
they are told.

## 6. Operational responsibilities

| Task | Who | How often | Procedure |
|---|---|---|---|
| Watch alerts | Delivery | Continuous while deployed | `infra/docs/RUNBOOK.md` → Alert response |
| Review spend against budget | Delivery, reported to sponsor | Weekly during pilot | Budget ledger |
| Review quality numbers | Support lead + delivery | Weekly during pilot | `python -m scripts.quality_check` |
| Rotate the AI provider key | Delivery, with client approval | Per client policy | RUNBOOK → Replacing the AI provider key |
| Rotate the mailbox vault key | Delivery | Per client policy | RUNBOOK → Rotating the mailbox token key |
| Verify a restore actually works | Delivery | Before launch, then quarterly | RUNBOOK → Database restore |
| Access review — who has which role | Client Admin | Monthly | The people page |
| Dependency advisories | Delivery | Every CI run, reviewed weekly | `make security-check` |

A restore that has never been tested is a backup, not a recovery plan. The
quarterly drill is the point of that row.

## 7. Boundaries worth stating plainly

- **The product never sends mail.** No support request can change that; it
  would be a new authorization and a code change, not a setting.
- **We do not have standing access to client data.** Operational access is for
  a named purpose, and reads of the audit trail are themselves recorded.
- **Nothing from this deployment reaches the SaaS product** ResolveFlow is
  separately building. There is no export path.
- **We will say when we do not know.** A guess during an incident is worse than
  "investigating, nothing ruled out yet".

## 8. Related documents

| Need | File |
|---|---|
| What to do during an incident | `infra/docs/INCIDENT_RESPONSE.md` |
| Deploys, rollbacks, restores, rotation | `infra/docs/RUNBOOK.md` |
| What the system holds and how to remove it | `docs/PRIVACY.md` |
| Acceptance criteria for the pilot | `docs/UAT_CRITERIA.md` |
| Whether we are ready for production | `docs/GO_NO_GO.md` |
