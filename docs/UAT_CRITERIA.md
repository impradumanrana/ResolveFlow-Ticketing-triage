# User Acceptance Criteria — Staging Pilot

**Audience:** the client's acceptance team — the sponsor, the support lead, and
the data-protection lead — and the delivery team running the pilot.

**Purpose:** to agree, *before* the pilot runs, what "it works" means. Agreeing
afterwards is how a pilot becomes an argument.

**Status:** criteria drafted and ready for signature. **Nothing has been run
against real mail**, because no staging environment is deployed and no mailbox
is connected. The two sign-off blocks at the end are deliberately empty.

---

## 1. What this pilot is, and is not

**It is:** a period in which the system reads real support mail, triages it, and
recommends an answer, while every answer is read by a person who decides what
happens. The question being answered is whether its recommendations are good
enough, and safe enough, to be worth a reviewer's time.

**It is not:** a test of whether the software runs. That is already established
and evidenced in the phase reports. The pilot tests the *judgement* of the
system against the client's own standards, which only the client can supply.

**It does not send anything.** In Observe Mode nothing is written to the
mailbox at all. In the later Draft stage an approved answer becomes a Gmail
draft that a person opens and sends themselves. There is no stage in which the
product sends mail.

## 2. Before the pilot can start

Each of these is a client input or a client decision. None can be substituted.

| # | Precondition | Owner | Why it blocks |
|---|---|---|---|
| 1 | Staging deployment authorized, with a GCP project and billing | Client sponsor | Nothing is deployed without explicit authorization |
| 2 | AI provider, models, region and monthly budget approved | Client sponsor + DPO | No model is called until a provider is approved; region is a data-residency decision |
| 3 | Gmail OAuth client created as an *Internal* app, redirect URI registered | Workspace admin | Internal avoids Google's restricted-scope review; External takes weeks |
| 4 | A test mailbox named, and a Workspace admin available to consent as it | Workspace admin | The product authorizes one mailbox at a time |
| 5 | Approved knowledge content, and who may upload it | Support lead | Answers must be grounded in the client's own approved guidance |
| 6 | **A labelled case set: 30+ real conversations with the category, urgency and the answer the client considers correct** | Support lead | Without the client's labels, "accuracy" measures agreement with our own judgement, which proves nothing |
| 7 | Named UAT participants, one per role | Support lead | §4 is per role |
| 8 | Retention periods per data class, and the audit retention position | DPO | Needed before real customer data is ingested |

**Precondition 6 is the one most often skipped and the one that matters most.**
Everything in §3 is unmeasurable without it.

## 3. Acceptance thresholds

These are the numbers the pilot is judged against. They are the C11 Quality
Check gates, versioned in `app/fixtures/quality/thresholds_v1.json`, and
changed only by changing that file in a reviewed commit.

| Gate | Metric | Threshold | What a failure means |
|---|---|---|---|
| Classification | category accuracy | ≥ 85% | Conversations reach the wrong queue |
| Classification | urgency accuracy | ≥ 70% | SLA targets are set wrongly |
| Retrieval | recall@1 | ≥ 80% | The right article is not found first |
| Retrieval | recall@3 | ≥ 90% | The right article is not found at all |
| Retrieval | MRR | ≥ 0.85 | The right article ranks poorly |
| Groundedness | validated share | **100%** | An answer was not checked against its sources |
| Groundedness | citation validity | **100%** | An answer cited something that does not say that |
| Safety | risky never auto-resolved | **100%** | A conversation needing a person was closed without one |
| Safety | guardrail recall | ≥ 90% | A risk signal was missed |
| Latency | p95 | ≤ 20s | Reviewers wait |
| Cost | mean per conversation | ≤ 200,000 micro-units | The budget will not hold |

Three of these are **100% and not negotiable**: the two groundedness gates and
"risky never auto-resolved". A single failure in any of them stops the pilot,
because each one is a case where a customer could have been told something
untrue or a serious problem could have been closed unseen.

The others are quality thresholds. Missing one is a finding to fix, not
necessarily a stop.

### How accuracy will be measured

Against precondition 6, not against our fixtures. The client's labels are
ground truth; the system's output is compared to them; and the comparison is
run by `scripts/quality_check.py --live`, which refuses to run without
explicit spend authorization.

**What the existing offline numbers do and do not show.** The offline Quality
Check passes all eleven gates with accuracy at 1.0. That number means the
pipeline is wired correctly end to end — it does not mean the model is
accurate, because offline the provider is deterministic and keyword-driven and
the ten fixture cases are the ones it was built against. Treating that 1.0 as a
prediction of real accuracy would be misleading, and this document says so
rather than quoting it.

## 4. Role walkthroughs

Each participant signs that their script passed. Steps marked **(must refuse)**
are the important ones: a product that cannot do the wrong thing is worth more
than one that does the right thing quickly.

### Agent

| # | Step | Expected |
|---|---|---|
| A1 | Sign in with a Google account in the client's domain | The workspace opens, showing only mailboxes they have permission for |
| A2 | Open a triaged conversation | The route, category, urgency, the recommended answer, and its citations |
| A3 | Follow a citation to the article | The quoted sentence appears in the article, verbatim |
| A4 | Approve a grounded recommendation | Recorded as approved. **In Observe Mode: no draft, and the screen says why** |
| A5 | Edit the recommendation, then approve | Recorded as the agent's own words, as a new revision; the model's original is still visible |
| A6 | Reject without a reason | **(must refuse)** It asks for a reason |
| A7 | Approve a conversation whose recommendation is ungrounded | **(must refuse)** It explains that it cannot be approved as the model's words, and offers editing |
| A8 | Open a conversation in a mailbox they lack permission for, by URL | **(must refuse)** Not found — not "forbidden", which would confirm it exists |
| A9 | Have two agents approve the same conversation at once | One succeeds; the other is told it changed and to look again |
| A10 | Try to reroute or reassign | **(must refuse)** Not an Agent's permission |

### Supervisor

| # | Step | Expected |
|---|---|---|
| S1 | Reroute a conversation to another queue | Recorded, with the before and after version |
| S2 | Assign a conversation to an agent | Recorded |
| S3 | Bulk assign a selection | Up to the bulk maximum; beyond it, refused with a clear message |
| S4 | Bulk assign repeatedly and quickly | **(must refuse)** Rate limited, with how long to wait |
| S5 | Act on a conversation in a department they are not in | **(must refuse)** Not found |
| S6 | Change AI settings | **(must refuse)** Not a Supervisor's permission |

### Admin

| # | Step | Expected |
|---|---|---|
| D1 | Invite a colleague and set their role | They can sign in with exactly that role |
| D2 | Connect the test mailbox, signed in as the mailbox | Connected; the address shown matches |
| D3 | Connect it while signed in as the wrong account | **(must refuse)** Wrong mailbox; the grant is revoked at Google, not merely discarded |
| D4 | Untick a scope on Google's consent screen | Refused if the mailbox scope was removed; connected read-only if only compose was unticked |
| D5 | Disconnect the mailbox | The credential is destroyed immediately, even if Google is unreachable |
| D6 | Set the AI provider, model, region and budget | Saved; verification reports which key is in use by its last four characters only |
| D7 | Enter a provider key with a trailing newline | Accepted and trimmed, or clearly refused — never stored broken |
| D8 | Transfer ownership, or set retention | **(must refuse)** Owner only |

### Knowledge Manager

| # | Step | Expected |
|---|---|---|
| K1 | Upload approved content | Indexed; the article count and corpus fingerprint change |
| K2 | Upload a document with no usable text | Refused, naming the file and the reason |
| K3 | Open a ticket | **(must refuse)** No ticket access at all |
| K4 | Re-run the Quality Check after an upload | Numbers change, and the report says the corpus changed since the last run |

### Auditor

| # | Step | Expected |
|---|---|---|
| U1 | Read any conversation in the organization | Visible, including the decision history |
| U2 | Approve, edit, reject, assign or reroute anything | **(must refuse)** Read-only role, every time |
| U3 | Read the audit trail for a conversation | Who did what, when, with the reason and both versions — refusals included |

### Owner

| # | Step | Expected |
|---|---|---|
| O1 | Set retention per data class | Saved and versioned |
| O2 | Place a legal hold, then run a retention sweep | The sweep refuses for the held class |
| O3 | Run a data-subject export for a test customer | Their data, with other participants' addresses redacted |
| O4 | Run an erasure for that customer, then look at the conversation | Content gone; who-did-what intact; the conversation no longer listed |
| O5 | Run the same erasure again | Reported as already erased, not run twice |
| O6 | Attempt an erasure while a legal hold is active | **(must refuse)** Hold active |

## 5. Operational acceptance

Measured over the pilot period, not in a single sitting.

| # | Criterion | Target |
|---|---|---|
| P1 | Conversations triaged without operator intervention | ≥ 95% |
| P2 | Reviewers' agreement that a recommendation was one they would have sent | ≥ 80%, **on a sample the client chooses** |
| P3 | Conversations reaching a person | Recorded, not targeted — it is an input to staffing, not a score |
| P4 | Unplanned outages | 0 |
| P5 | Spend against the approved monthly budget | Within budget at observed volume |
| P6 | Incidents involving customer data | 0 |

P2 deserves emphasis: the sample must be the client's. A sample we choose
measures our own taste.

## 6. Leaving Observe Mode

Advancing to the Draft stage requires every condition below. They are the same
list the code carries in `app/pilot.py`, and a test asserts the two agree, so
this document cannot drift from what the system enforces.

1. At least two weeks of real conversations have been triaged in Observe Mode.
2. The Quality Check passes on the client's own labelled cases, against
   thresholds agreed before the run.
3. Reviewers agree the recommended answers are ones they would have sent, for a
   sample they chose rather than one we chose.
4. No conversation was routed AUTO_RESOLVE that a reviewer judged unsafe to
   resolve.
5. Cost per conversation is within the approved monthly budget at the observed
   volume.
6. The client has authorized `gmail.compose`, and a Workspace administrator has
   re-consented the mailbox (see docs/MAILBOX_CONNECTION_MODES.md).

## 7. What happens when something fails

- **A 100% gate fails** → the pilot stops. The cause is found and fixed, a test
  is added that fails without the fix, and the pilot restarts from the
  beginning of its measurement window.
- **A quality threshold is missed** → a finding, with a cause and a proposal.
  The client decides whether to continue measuring or pause.
- **A role walkthrough step fails** → a defect, fixed and re-walked by the same
  participant. A step marked **(must refuse)** that did *not* refuse is treated
  as a 100% gate failure.
- **Anything involving customer data** → `infra/docs/INCIDENT_RESPONSE.md`,
  immediately, before diagnosing.

## 8. Sign-off

Signing §3 and §4 before the pilot means the criteria are agreed. Signing §9
after it means they were met.

### Criteria agreed (before the pilot)

| Role | Name | Date | Signature |
|---|---|---|---|
| Client sponsor | | | |
| Support lead | | | |
| Data-protection lead | | | |
| Delivery (ResolveFlow) | | | |

## 9. Acceptance (after the pilot)

To be completed with the measured results attached, and with
`docs/AI_PERFORMANCE_REPORT.md` filled in from the live run.

| Role | Name | Date | Outcome (accept / accept with findings / reject) | Signature |
|---|---|---|---|---|
| Client sponsor | | | | |
| Support lead | | | | |
| Data-protection lead | | | | |
| Delivery (ResolveFlow) | | | | |

**Findings carried into production** (each needs an owner and a date, or it is
not carried, it is forgotten):

| # | Finding | Severity | Owner | Due |
|---|---|---|---|---|
| | | | | |
