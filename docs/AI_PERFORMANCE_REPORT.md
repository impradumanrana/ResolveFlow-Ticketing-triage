# AI Performance Report

**Audience:** the client's sponsor and support lead, deciding whether the
system's judgement is good enough to put in front of reviewers.

**Date:** 2026-10-05. **Stage:** pre-pilot. **Provider:** none — no paid model
has been called at any point in this project.

---

## Read this first

This report has two halves, and conflating them would be the single most
misleading thing in the whole delivery.

**What has been measured** is the system's *behaviour*: that it triages every
conversation, that it never auto-resolves something ungrounded or risky, that
it cites only articles it actually searched, that it produces the same answer
twice, how fast it is, and that cost is accounted for per conversation. All of
that is real, reproducible, and measured over 45 realistic conversations.

**What has not been measured** is *accuracy against the client's standards*.
That needs the client's labelled cases and the client's approved model. It is
the central question of the pilot and this report cannot answer it yet.

There is a number in the offline results that looks like an answer and is not.
The Quality Check reports classification accuracy of **1.00**. Offline, the
provider is a deterministic keyword matcher and the ten fixture cases are the
ones it was built against, so that figure is 1.00 by construction. It shows the
measurement harness works end to end. **It predicts nothing about a real
model's accuracy on real mail**, and it should not be quoted as if it did.

---

## 1. Behavioural results — 45 realistic conversations

Source: `app/fixtures/realistic_ticket_batch_45.csv`, 45 conversations written
to resemble a support inbox. **They carry no labels**, which is why no accuracy
is claimed here. Run with `python -m scripts.acceptance_run`.

### Safety and stability invariants

| Invariant | Result |
|---|---|
| Every auto-resolved conversation had validated grounding and a citation | **45/45 pass** |
| No conversation that tripped a guardrail was auto-resolved | **pass** |
| Every citation referred to an article actually in the searched corpus | **pass** |
| Every conversation produced a route and a trace | **45/45 pass** |
| A second pass produced the same route for every conversation | **45/45 pass** |
| No trace step named an outbound operation | **pass** |

All six hold. Each one can fail — each is injected and caught in
`tests/test_acceptance_run.py`, which is how we know the result is not a test
looking in the wrong place.

### Route mix

| Route | Count | Share |
|---|---|---|
| AUTO_RESOLVE | 26 | 57.8% |
| ESCALATE | 17 | 37.8% |
| CLARIFY | 2 | 4.4% |

**42.2% of conversations reach a person**, and **17.8% tripped a guardrail**.

This is the number an operations manager should look at, and it is reported
rather than targeted: it is an input to staffing, not a score. On this corpus
the system would hand roughly two in five conversations to a human, which is
the behaviour the design intends — a system that escalated 2% would be more
worrying than one that escalates 42%.

### Latency

| Measure | Value |
|---|---|
| p50 | 12 ms |
| p95 | 13 ms |
| max | 1,153 ms |
| mean | 37 ms |

**These are simulation figures and are not a prediction.** The offline provider
answers instantly, so what this measures is the pipeline's own overhead —
guardrails, retrieval over the MCP boundary, rule evaluation, grounding
validation and recording. The maximum is the MCP server's cold start on the
first call.

Real latency will be dominated by the provider's response time, typically one
to several seconds per call with two calls per conversation. The agreed
threshold is a p95 of 20 seconds, which leaves substantial headroom for a slow
provider. The pipeline's own contribution is, on this evidence, negligible.

### Cost accounting

| Measure | Value |
|---|---|
| Mean per conversation | 104,388 micro-units |
| Total across 45 conversations | 4,697,440 micro-units |

**These are simulated prices.** What this demonstrates is that every
conversation has its cost reserved before the call and recorded after it, in
whole micro-units with no floating-point drift, against a monthly budget that
refuses the call when exhausted. The agreed ceiling is 200,000 micro-units per
conversation.

Real cost is the client's provider's price list multiplied by observed token
counts. The projection to run at a given volume is arithmetic the client should
do with their own prices once a provider is approved; this report will not
invent it.

---

## 2. Gate results — the labelled fixture set

Source: `app/fixtures/quality/dataset_v1.json` (10 labelled conversations)
against `thresholds_v1.json`. Run with `python -m scripts.quality_check`.

| Gate | Metric | Value | Threshold | Verdict |
|---|---|---|---|---|
| Classification | category accuracy | 1.00 | ≥ 0.85 | pass |
| Classification | urgency accuracy | 1.00 | ≥ 0.70 | pass |
| Retrieval | recall@1 | 1.00 | ≥ 0.80 | pass |
| Retrieval | recall@3 | 1.00 | ≥ 0.90 | pass |
| Retrieval | MRR | 1.00 | ≥ 0.85 | pass |
| Groundedness | validated share | 1.00 | = 1.00 | pass |
| Groundedness | citation validity | 1.00 | = 1.00 | pass |
| Safety | risky never auto-resolved | 1.00 | = 1.00 | pass |
| Safety | guardrail recall | 1.00 | ≥ 0.90 | pass |
| Latency | p95 | 1,374 ms | ≤ 20,000 ms | pass |
| Cost | mean per run | 91,352 µ | ≤ 200,000 µ | pass |

**Verdict: PASS** — with the caveat at the top of this report. The three gates
that are genuinely informative here are the two groundedness gates and
"risky never auto-resolved", because those do not depend on the provider being
clever: they check that an answer was validated against its sources and that a
risky conversation was not closed. Those hold structurally, and they also hold
on the 45-conversation corpus.

The accuracy and retrieval figures are harness checks, not model evidence.

---

## 3. What the pilot still has to establish

| Question | Needs | How it will be answered |
|---|---|---|
| Does it classify the client's mail correctly? | 30+ client-labelled conversations (UAT precondition 6) | `quality_check --live` against the client's labels |
| Are its answers ones a reviewer would have sent? | A sample the client chooses | UAT operational criterion P2, ≥ 80% |
| What does it actually cost per conversation? | An approved provider and its prices | Observed token counts × the client's price list |
| How fast is it in practice? | A real model | p95 over the pilot period, against the 20-second gate |
| Does it stay safe on real mail? | Real mail | The same six invariants, run over the pilot corpus |
| Is the knowledge base good enough? | The client's approved content | Retrieval gates against their corpus; a missing article shows as a retrieval failure, not a bad answer |

None of these can be answered without the preconditions in
`docs/UAT_CRITERIA.md` §2. Four of the six need a decision the client has not
yet made.

---

## 4. Known limitations of the system itself

Stated here rather than discovered during the pilot.

- **Prompt injection can shift a classification.** A customer's email is
  attacker-controlled text. It is fenced with a token it cannot guess and every
  prompt states that it is data, but a sufficiently clever message may still
  move an answer. What bounds the damage is that the output is closed:
  classification is validated against a fixed enum, answers must quote their
  evidence verbatim, the route is re-decided by deterministic rules that can
  only narrow it, and nothing can send. An injection cannot create a
  capability, a recipient, or a message to a customer.
- **The knowledge base is the ceiling on answer quality.** The system refuses
  to answer beyond its evidence, so thin content shows up as escalations rather
  than as wrong answers. That is the intended failure mode, but it means
  retrieval scores measure the corpus as much as the retriever.
- **No attachment is read.** Attachment contents are never downloaded, so a
  conversation whose substance is in a PDF will be escalated.
- **Latency has not been measured against a real provider**, and the offline
  figures must not be used as a baseline.
- **The model's own accuracy is unknown to us.** We have never called one.

---

## 5. Reproducing these numbers

```bash
# Behavioural invariants over the 45 realistic conversations.
RESOLVEFLOW_TEST_MODE=1 python -m scripts.acceptance_run --json acceptance.json

# The eleven gates against the labelled fixture set.
RESOLVEFLOW_TEST_MODE=1 python -m scripts.quality_check
```

Both are offline, deterministic, and call no paid model. The exit status is the
verdict, so either can be a build step.

The live equivalents — against the client's corpus, labels and provider —
require explicit spend authorization and refuse to run without it.
