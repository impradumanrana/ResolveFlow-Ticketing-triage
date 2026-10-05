# ResolveFlow AI - C11 Verification Report

**Phase:** C11 - Triage, grounded drafting, and quality

**Result:** GREEN

**Date:** 4 October 2026

**Gate:** "Versioned evaluation thresholds pass; a demo visibly shows request -> MCP call -> knowledge response -> grounded answer -> validator."

## Scope and safety

**No paid model was called, and no request left this machine.** Every model answer came from the offline provider, which derives its output from the prompt it is given: classification from the MVP's own deterministic classifier, and the grounded draft from the evidence the prompt actually carries. No cloud resource was created, no mailbox was connected, and nothing was sent to a customer - the pipeline runs in Observe Mode (C-D010).

Docker remained unavailable, so live verification used a throwaway PostgreSQL 17 in the session scratchpad with **exactly three** pgvector statements excluded, each asserted to occur once. Consequently the live run exercised the pipeline's storage with the fixture-backed knowledge server; the pgvector retrieval behind it was verified on a real pgvector instance in C05, and the production backend is the same `app.knowledge.retrieval.search` C05 proved. The database, both local servers, and the browser artifacts were removed afterwards.

## Delivered

- **`app/triage/pipeline.py`** - the production pipeline. The MVP workflow is compiled and run, not reimplemented; its two seams are filled with the C10 gateway and the C05 corpus. Around it, C09's rules decide the route again from the same evidence, the client's routing is applied, service-level targets are computed, and the run is recorded.
- **`app/triage/knowledge_mcp.py`** - the knowledge base served across the MCP stdio boundary, backed by the client's PostgreSQL corpus, with a test-only fixture backend for offline runs.
- **`app/triage/mcp_client.py`** - a persistent client: one server process, reused, with a deadline on every call and a failed session replaced rather than reused.
- **`app/triage/store.py`** - one transaction per run: the run, its action, the routing it applied, and its targets.
- **`app/triage/quality.py`** and **`quality_store.py`** - the Quality Check: six gates, versioned thresholds, a labelled dataset, and results recorded per gate against the corpus fingerprint they measured.
- **`scripts/triage_demo.py`** and **`scripts/quality_check.py`** - the demonstration and the check, offline by default, each refusing a live run unless the client's spend is explicitly authorized. `make triage-demo`, `make quality-check`.
- **Workspace** - `/workspace/quality`: verdict, gates in priority order with their own units, what failed, and whether the knowledge has changed since the run.
- **No migration.** C04's `triage_runs`, `actions`, `evaluation_runs`, and `evaluation_results` were designed for this; cost per run joins from C10's `provider_calls` by correlation id.

## The gate

### Versioned thresholds pass

`dataset_v1.json` (10 labelled conversations) against `thresholds_v1.json` (11 thresholds across 6 gates), run through the real pipeline:

| Gate | Measure | Result | Needs |
|---|---|---|---|
| classification | category accuracy | 100% | ≥ 85% |
| classification | urgency accuracy | 100% | ≥ 70% |
| retrieval | recall@1 / recall@3 / MRR | 100% / 100% / 100% | ≥ 80% / 90% / 85% |
| groundedness | validated share / citation validity | 100% / 100% | 100% / 100% |
| safety | risky never auto-resolved / guardrail recall | 100% / 100% | 100% / ≥ 90% |
| latency | p95 | 1.2 s | ≤ 20 s |
| cost | mean per run | 0.084 minor units | ≤ 0.2 |

No gate is vacuous, and a test asserts it: five conversations were answered automatically (so groundedness measured something), four were risky (safety), five carry an expected article (retrieval), and every run has a real cost and latency. Each gate was also deliberately broken to confirm it can fail.

### The demonstration shows the chain

`make triage-demo` prints nine numbered stages with real values at each: the request; the model call through the gateway with its classification; the guardrail codes; the **MCP call** (tool name, `stdio` transport, the exact tool arguments); the **knowledge response** (KB-001 at 0.908 with its stored text); the decision with the knowledge score against the 0.55 threshold; the **grounded answer** carrying `[KB-001]`; the **validator** verdict (`valid: true`, one verified claim); and what was recorded, including tokens, cost, and latency. It ends with "Nothing was sent to the customer."

A test runs the demonstration and asserts each stage appears, in order, with those values - and that a risky conversation instead shows the review note and `ESCALATE`.

## Automated gate evidence

| Check | Result |
|---|---|
| `make check` | **exit 0** |
| Python suite | **1,344 passed** (1,248 at C10) |
| Web suite | **158 passed** (146 at C10) |
| New: the MCP boundary | 16 tests |
| New: the pipeline | 25 tests |
| New: what a run writes | 16 tests |
| New: Quality Check | 23 tests |
| New: the quality endpoint | 8 tests |
| New: demonstration and CLI | 8 tests |
| New: the quality page | 12 web tests |
| Ruff, mypy (`app/triage` added), ESLint, TypeScript, Next.js build, infrastructure checks | clean |

### Mutation check

Twenty deliberate defects, injected one at a time: rules unable to narrow the route, a withheld draft stored anyway, narrowing picking the first answer, a second correlation id in the trace, a knowledge outage returning nothing instead of failing, a failed session reused, the corpus filtering by category, an invalid result shape accepted, an unmeasured metric passing, citation validity unchecked, retrieval paired by query text, an interpolating percentile, a rerun forking the history, any string reaching a foreign key, an unusable policy reference written, a breached target overwritten, the endpoint skipping its role check, an unmeasured metric reported as a number, a provider failure escaping the pipeline, and the demonstration hiding the validator.

**20/20 caught.** One survived the first pass - "a failed session is reused" - because the test closed the session itself instead of letting the client's own cleanup run. The test now induces a real failure and asserts the session is dropped and the next call reconnects.

## Defects found during the phase

1. **Retrieval metrics collapsed cases with identical wording.** Results were paired to cases by query text, so two conversations asking the same question scored against one result set, and the rest counted as misses. Found by a metric test with three same-worded cases; now paired by position.
2. **An SLA policy reference that is not a UUID lost the whole run.** Found in the live run: the identifier went straight into a UUID column and the exception propagated out of `record_run`. The store already guarded assignment columns this way; the target is now recorded without its policy rather than discarding the run.
3. **A failing-session test proved nothing** (above).
4. **Two seeded provider configurations made the gateway refuse everything.** Not a defect: the browser seed added a second active provider, and C10 refuses an ambiguous configuration with `CONFIG_MISSING`. Visible immediately in `provider_calls`, which is what that log is for.

## Live PostgreSQL evidence

Chain applied to `20260916_0008`; **26/26 checks passed**.

- An answerable conversation stored one run with route, category, citations `{KB-001}`, validated grounding, an eight-event trace, tokens, and model.
- Its action row is `TRIAGE_RUN`, `SUCCEEDED`, with the run's cost in its payload.
- The ticket took the route, category, department, and queue the rules decided, and its version was bumped once.
- The service-level target was stored against the real policy row.
- `provider_calls` joins the run by correlation id, and the summed cost equals the run's cost exactly; daily usage was metered.
- Re-recording the same run updated its row and did not duplicate its action.
- A risky conversation stored `ESCALATE` with no citations, no validated grounding, and its reasons.
- The database refused, each on its named constraint: an auto-resolve draft without grounding, an auto-resolve draft without a citation, an unknown route, and a duplicate correlation id.
- A Quality Check recorded `COMPLETED` with a verdict, its corpus fingerprint, its article count, the membership that asked for it, and one row per gate with detail; `latest()` read it back.
- An unmeasured metric was stored as `-1` and failed - never as a missing row that would read as a pass.
- Another organization saw no runs and no evaluations, and no provider key appeared anywhere the pipeline wrote.

## Browser evidence

Driven with Playwright against a local dev server and a scratch database seeded by running the real pipeline over four conversations (`AUTO_RESOLVE`, `ESCALATE`, `CLARIFY`, `AUTO_RESOLVE`) plus a recorded Quality Check.

| Checked | Result |
|---|---|
| Quality page, Owner | *Passed* badge, verdict, measured time, dataset and threshold versions, corpus fingerprint and article count |
| Gate tables | Six gates in priority order - safety and groundedness first - each metric with its own units: percentages, `48 ms`, `0.0837 minor units` |
| Knowledge changed | Adding an article showed "The knowledge has changed since this run… Run the check again", and the verdict correctly stayed *Passed* |
| A failing run | *Failed* badge and a "What failed" panel naming both failures, including an unmeasured metric shown as "not measured" rather than a number |
| Ticket workspace (C08) | The conversation shows `AUTO_RESOLVE`, the draft with `[KB-001]`, its citation, "triage run · system" in the action history, and all eight pipeline steps in the trace with the MCP step timed |
| An escalated conversation | `ESCALATE` with its controls listed, the review note in place of an answer, and the standing reminder that nothing is sent without approval |
| Supervisor | Sees the page (quality.view) |
| Agent | No navigation link; the direct URL is refused and recorded |
| Phone width (390px) | No horizontal scrolling, nothing overflows |
| Console | No errors or warnings |

## Residual risks and open items

| Item | Status |
|---|---|
| The pgvector-backed knowledge server was not exercised live in this phase | This host has no pgvector and Docker is unavailable. C05 verified the same retrieval function live on pgvector 0.8.6; re-run the live Quality Check against the client's corpus when a pgvector instance is available |
| A Quality Check cannot be started from the workspace | Deliberate: a run calls the client's paid model for every case and needs the worker to run asynchronously. It is a command-line or build step until then |
| The pipeline is not yet triggered by ingestion | C07 queues the work and C11 performs it; wiring the worker that joins them needs the deployment that is still blocked on client inputs |
| Thresholds are pilot values | Agreed values are a client input. They are versioned, so changing them is a visible change of `threshold_version`, not an edit |
| The offline provider is not a model | It exercises the real classifier, retrieval, validator, and rules, and nothing else. Accuracy against a hosted model is unmeasured until the client authorizes a live run |
| Cost per run is an estimate at the client's prices | Labelled as such everywhere it appears (C-D102) |
| Eleven phases remain uncommitted | Recommended before C12 |
