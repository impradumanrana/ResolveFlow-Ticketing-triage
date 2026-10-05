# ResolveFlow AI — C14 Verification Report

**Phase:** C14 — Staging pilot and acceptance.

**Gate:** signed UAT criteria, AI report, runbook, support contacts, and
production go/no-go exist. **All five exist.** One of them — the UAT criteria —
exists unsigned, because signing it is the client's act and is itself the first
precondition of the pilot.

**Date:** 2026-10-05.

---

## Read this first: what this phase could and could not do

C14's activities are "deploy staging, ingest approved knowledge, connect test
mailboxes, run synthetic plus client-approved cases, measure accuracy, latency
and cost, conduct role UAT, train users, and run Observe Mode before drafts."

**Five of those eight are not mine to do.** They need authorizations and inputs
only the client can give, and the standing instruction for this delivery is to
create no cloud resource, connect no mailbox, and call no paid model without
explicit approval. None has been given.

| Activity | Status |
|---|---|
| Deploy staging | **Blocked** — needs authorization, a GCP project and billing |
| Ingest approved knowledge | **Blocked** — needs the client's content |
| Connect test mailboxes | **Blocked** — needs the Workspace administrator and the client's OAuth client |
| Measure accuracy against client cases | **Blocked** — needs an approved provider and 30+ client-labelled conversations |
| Role UAT by the client's people | **Blocked** — needs their participants |
| Run synthetic cases | **Done** — 45 conversations, offline, measured |
| Define and enforce Observe Mode | **Done** — named, reportable, with agreed exit criteria |
| Prepare the acceptance artifacts | **Done** — all five gate documents |

So this phase delivered the measurable work that needs nothing from the client,
and prepared everything the pilot will run on. It did not run a pilot, and this
report does not pretend one happened.

## Delivered

| Piece | What it is |
|---|---|
| `docs/UAT_CRITERIA.md` | Preconditions, acceptance thresholds, six role walkthroughs, operational criteria, Observe Mode exit, and two sign-off blocks |
| `docs/AI_PERFORMANCE_REPORT.md` | What has been measured, what has not, and why the offline accuracy figure must not be quoted as a result |
| `docs/SUPPORT.md` | Severities, response targets, escalation, and scope boundaries — with contacts deliberately blank |
| `docs/GO_NO_GO.md` | 44 checklist items across seven sections, each Ready, Blocked or Pending pilot, with who unblocks it |
| `infra/docs/RUNBOOK.md` | Four new procedures: data-subject requests, retention sweeps, turning the pilot stage, running the pilot measurements |
| `app/pilot.py` | Two pilot stages, derived from the scope profile, with exit criteria as code |
| `scripts/acceptance_run.py` | The pilot acceptance run: six invariants, route mix, latency, cost |

## The pilot acceptance run

45 realistic conversations from `app/fixtures/realistic_ticket_batch_45.csv`,
offline, deterministic. **The corpus carries no labels, so no accuracy is
claimed** — a test asserts the file has no label columns, so adding them later
has to be a deliberate decision rather than an inherited fixture edit.

### Invariants — 6/6 pass

| Invariant | Result |
|---|---|
| Every auto-resolved conversation had validated grounding and a citation | pass |
| No conversation that tripped a guardrail was auto-resolved | pass |
| Every citation referred to an article in the corpus searched | pass |
| Every conversation produced a route and a trace | pass |
| A second pass produced the same route for every conversation | pass |
| No trace step named an outbound operation | pass |

Each can fail: every one is injected and caught in
`tests/test_acceptance_run.py`, which is why the result means something.

### What the run reports

| Measure | Value |
|---|---|
| Route mix | AUTO_RESOLVE 26 (57.8%), ESCALATE 17 (37.8%), CLARIFY 2 (4.4%) |
| Reaches a person | **42.2%** |
| Tripped a guardrail | 17.8% |
| Latency p50 / p95 / max | 12 ms / 13 ms / 1,153 ms |
| Cost per conversation | 104,388 micro-units (simulated prices) |

The latency and cost figures are **simulation**, not prediction: offline the
provider answers instantly, so what is measured is the pipeline's own overhead,
and prices are the fixture's. They are reported because they show the pipeline
contributes negligibly to latency and that cost is accounted for per
conversation — not because they forecast production.

## Observe Mode

The posture existed after C13 but had no name, no way to confirm it from
outside, and no stated condition for advancing.

- Two stages, `OBSERVE` and `DRAFT`, derived from C13's scope profile so there
  is one mechanism rather than a second mode flag.
- **There is no third stage.** Sending is absent from the product, and
  `automatic_sending` stays a published constant rather than a setting.
- Six exit criteria, carried as a list in `app/pilot.py` and mirrored in
  `docs/UAT_CRITERIA.md` §6, **with a test asserting the two cannot drift.**
  That test immediately caught the document dropping a parenthetical the code
  had.
- `GET /v1/capabilities` now reports `pilot_stage` and a real `observe_mode`,
  so a UAT step can confirm the posture from outside.

### A defect this uncovered

`observe_mode` was published as a literal `true`. That became **untrue** the
moment C12a made drafting possible: under the drafting profile the system does
write to the mailbox, while the API kept asserting it did not. Nothing consumed
the field yet, so the fix was clean — it is now derived from the posture. A
contract test asserts it is an ordinary boolean while `automatic_sending`
remains a `const false`.

## Automated evidence

| Check | Result |
|---|---|
| Python tests | **1,872 passed** |
| Web tests | **200 passed** |
| `ruff`, `mypy` | clean on 85 source files |
| New test modules | `test_acceptance_run` (28), `test_pilot` (20) |
| Acceptance run | 6/6 invariants over 45 conversations |
| Quality Check | 11/11 gates (with the circularity stated in the AI report) |

### Mutation check

**29 mutants, all 29 caught.** Each is one edit that makes a control wrong, applied to the real
source or document and reverted afterwards.

Caught include: a third pilot stage appearing; a fresh deployment starting in
the drafting stage; `observe_mode` being asserted rather than derived;
`automatic_sending` becoming configurable; the exit criteria being emptied, or
losing the cost condition, the client-authorization condition, or the
requirement that the review sample be the client's own; a scope profile with no
decided stage; the contract reverting to a literal `observe_mode`; the endpoint
reporting a fixed stage; an ungrounded or risky auto-resolve being accepted; a
fabricated citation being accepted; reproducibility not being checked; the send
check being dropped or scanning prose again; identifiers matched without
splitting; a draft reply being treated as a send; the two passes zipped
loosely; the human share miscounted; the UAT document drifting from the code;
the support document inventing contacts.

**Four survivors from the first run were real test gaps, and were fixed:**

1. **Nothing asserted the report's own honesty flag.** `measures_accuracy`
   could be flipped to `True` and no test noticed — the field the entire
   framing of this report rests on. Now asserted, along with the note beside
   it, by running the script and reading its JSON.
2. **The latency fixture was already sorted**, so removing the sort changed
   nothing and the percentile test walked straight past it. Now deliberately
   out of order, with p50 and the mean pinned.
3. **`assert "NO-GO" in decision`** was satisfied by the phrase appearing later
   in the prose, so a mutant could rewrite the actual recommendation line. Now
   reads that line specifically.
4. **`assert "Who unblocks" in decision`** passed when a single table header
   was blanked, because six other tables still had it. Now counts headers
   against item tables.

The second and third are the same class of weakness: an assertion that happens
to be true for a reason other than the one intended. That is what a mutation
run is for, and it is why the four fixes matter more than the twenty-five
passes.

## Defects found during this phase

1. **`observe_mode` was a published falsehood** after C12a. Fixed by deriving
   it; see above.
2. **The "nothing sent" invariant scanned prose.** It searched each serialized
   trace step for "send" and failed on two conversations, because the
   knowledge-search step carries the query and a customer had written "please
   send me another link". The same mistake C12 made. It now reads step and tool
   *names* only.
3. **And then the fix was still wrong.** A word-boundary regex matches neither
   `send_reply` (an underscore is a word character) nor `sendMessage` (camelCase
   has no boundary) — precisely the shapes a real step would have. Caught by
   the positive control, which is the whole reason to write one. Identifiers
   are now split into words before matching; `draft_reply` and `sender_address`
   are deliberately not flagged.
4. **The UAT document had drifted from the code** by one parenthetical, caught
   by the sync test within a minute of writing it.
5. **The interrupted mutation run left a mutation applied.** A disk-full kill
   during the C13 mutation suite prevented a restore, leaving CI's blocking
   `npm audit` weakened to `npm audit || true`. Found because a C13 test
   asserts that exact line. Restored, and the full suite re-run to confirm
   nothing else was left modified — an argument for tests that assert on
   configuration, not only on code.

## What the client must supply before the pilot can run

Four items, three of which are decisions rather than work. They are the
critical path, and no further engineering moves the launch date until they are
made.

1. **Authorize staging**, with a GCP project, billing, a hostname and alert
   destinations.
2. **Approve an AI provider**, model, **region** and monthly budget. Region is
   a data-residency decision. Nothing calls a model before this.
3. **Create the Gmail OAuth client** as an Internal app and connect a test
   mailbox, as a Workspace administrator.
4. **Supply approved knowledge content, and 30+ labelled conversations** —
   real cases with the category, urgency and the answer the client considers
   correct.

Item 4 is the one most often skipped and the one that matters most: without
the client's labels, "accuracy" measures agreement with our own judgement.
Labelling the 45-conversation corpus ourselves was considered and rejected for
exactly that reason.

## Residual risks and open items

| Item | Status |
|---|---|
| **No pilot has run** | The gate's five artifacts exist; the acceptance they support has not happened. `docs/GO_NO_GO.md` records **NO-GO** with 21 blocked and 8 pending-pilot items |
| UAT criteria are unsigned | Signing is the client's act and the pilot's first precondition |
| Accuracy, real latency and real cost are unmeasured | Need an approved provider and the client's labels |
| Support contacts are blank | Deliberately. A support plan with placeholder names is not a support plan |
| Single-engineer support model | Stated in `docs/SUPPORT.md` §1 rather than implied. Round-the-clock cover is a different commercial arrangement |
| The offline accuracy figure of 1.00 is circular | Stated at the top of the AI report. It must not be quoted to the client as a prediction |
| No attachment is read | A conversation whose substance is in a PDF will escalate. Inherent, and worth telling reviewers |
| Knowledge quality is the ceiling on answer quality | Thin content shows up as escalations, not wrong answers. Retrieval scores measure the corpus as much as the retriever |
| Fourteen phases remain uncommitted | Recommended before C15 |
