# ResolveFlow AI - C09 Verification Report

**Phase:** C09 - Rules, routing, SLA, and guardrails

**Result:** GREEN

**Date:** 16 September 2026

**Gate:** "Golden rules, conflicts, time zones, SLA, and fail-safe review tests pass."

## Scope and safety

No cloud resource was created, no mailbox was connected, no request reached Google, and no paid model was called. Live verification used a throwaway PostgreSQL 17 in the session scratchpad with synthetic organizations (`acme`, `globex`), torn down afterwards.

Docker was still unavailable, so the real rendered migration chain was applied with **exactly three** pgvector statements excluded (the extension, the embedding column, and the HNSW index), each asserted to occur once. Migration `0007` does not touch pgvector.

## Delivered

- **`app/rules/business_hours.py`** - working-time arithmetic in the policy's own zone: weekends, holidays, custom working weeks, days ending at midnight, and both daylight-saving transitions.
- **`app/rules/sla.py`** - policy selection by specificity (queue > department > urgency, combined), ties reported as a conflict, first-response and resolution targets, and status (on track, at risk, breached, met, not applicable) with pause support.
- **`app/rules/routing.py`** - versioned client rules in a closed language: 10 matchable facts, 7 operators, 6 settable outcomes. Rules run in priority order, the first rule to set a field wins it, and equal-priority disagreement is a conflict.
- **`app/rules/engine.py`** - the three stages in order: the MVP guardrails (imported, unchanged), then client routing, then service levels. The final route uses the graph's exact precedence and can only be narrowed toward a person.
- **`app/rules/store.py`** - loads the rule set in force for one organization at one moment, publishes new rule versions (closing the previous one), manages VIP entries, and records SLA state idempotently.
- **Migration `20260916_0007`** - `routing_rules`, `vip_contacts`, `business_holidays`.
- **Workspace** - plain-English explanations for the four new rule codes.

## The safety property, and how it is held

> A client rule can send a conversation to a person. No client rule can send a conversation to the model.

| Mechanism | Evidence |
|---|---|
| Guardrails run first, imported from `app.guardrails`, with exactly the fact keys `app.graph.risk_guard` sends | Spy test on the call; source assertions against the graph |
| Route precedence is the graph's | 224 parametrized cases compare `base_decision` with the real `app.graph.decide` |
| Rules cannot set a decision, reply, or rule codes | Closed action vocabulary, pinned by test; refused at publish time |
| Rules can escalate but never de-escalate | 120 cases: every action × 3 conversations × 5 evidence states |
| Safety codes survive routing | Asserted as a subset relation in the same 120 cases |
| VIP status is not a safety exemption | A VIP threat still escalates |
| Anything unresolvable goes to a person | Rule conflict, invalid stored rule, or forced escalation blocks both auto-resolve and asking the customer. A service-level policy tie blocks auto-resolve |
| Missing evidence defaults to a person | `Evidence()` defaults are zero confidence and zero match |

## Automated gate evidence

| Check | Result |
|---|---|
| `make check` | **exit 0** |
| Python suite | **1,016 passed** (505 at C08) |
| Web suite | 126 passed (the rule-code test was extended) |
| New: time zones and SLA | 71 tests |
| New: golden rules, conflicts, and fail-safe | 422 tests |
| New: rule storage | 15 tests |
| Schema invariants | 3 new tests for migration `0007`; head moved to `20260916_0007` |
| Ruff, mypy (`app/rules` added), ESLint, TypeScript, Next.js build, infrastructure checks | clean |

### Mutation check

Tests that cannot fail prove nothing, so 14 deliberate defects were injected into the engine, router, SLA, and calendar, one at a time, and the gate suites were run against each.

| Injected defect | Result |
|---|---|
| Guardrails given the wrong fact key | caught |
| Narrowing step removed | caught |
| Clarify not blocked by rule problems | caught |
| Rule conflicts ignored | caught |
| Invalid stored rules ignored | caught |
| Precedence reordered | caught |
| Confidence floor loosened to 0.60 | caught |
| Rule order left to the database | caught |
| Wall-clock subtraction when adding minutes | **survived at first**; caught after two boundary tests were added |
| Wall-clock subtraction when measuring elapsed time | caught |
| SLA tie resolved by taking the first | caught |
| Holidays chosen before routing | caught |
| VIP domain matched by suffix | caught |
| At-risk boundary off by one | caught |

**14/14 caught.** The survivor showed that the spring test added exactly as many minutes as the shortened day holds, so the right and wrong arithmetic gave the same answer. The added tests use a target that fits the naive day but not the real one, and one that fits only the lengthened autumn day.

## Defects found during the phase

1. **The engine first sent the guardrails the wrong fact keys.** `evaluate_guardrails` reads `content`; the engine sent `subject`, `body`, and `text`. The guardrails saw empty text, fired only their empty-message fallback, and **a threat was routed to CLARIFY**. This was caught by a smoke run before any test was written. It is now pinned by a spy test and by assertions against the graph's own call.
2. **Daylight-saving arithmetic was wrong within a day.** Python subtracts and compares two datetimes that share a `ZoneInfo` by wall clock, ignoring their offsets. So 06:00 minus 00:00 on the day UK clocks go back reads as six hours, although seven elapsed. The calculator's docstring promised real elapsed time and did not deliver it. Every difference, sum, and comparison now happens in UTC. The earlier smoke tests had crossed clock changes only between days, so they could not see this.
3. **A working day ending at midnight lost a minute.** `day_end_minute = 1440` was clamped to 23:59. It now means the start of the next day.
4. **Publishing behind a scheduled version would leave two versions in force.** If a version starts at or after the publish time, it cannot be closed before it opens. That case is now refused.
5. **One test expectation was wrong, not the code.** Friday 16:00 + 60 working minutes lands exactly on Friday's 17:00 close, as another test in the same file already asserts. The expectation was corrected.

## Live PostgreSQL evidence

Chain applied to `20260916_0007`; **49/49 checks passed**. Every negative check had to fail on its *named* constraint or index, and each group has a positive control.

**Behaviour through the real store**

- Publishing returned versions 1, 2 (same organization) and 1 (other organization). Version 1's `effective_to` equals version 2's `effective_from` exactly.
- Loading at "now" returned only v2. Loading between T0 and T1 returned only v1. Loading before T0 returned nothing. Loading at the switch instant returned exactly one version.
- The other organization's rule and a policy scheduled for next month were not loaded.
- Publishing behind a scheduled version, and publishing a rule that tries to set `decision`, were both refused, and neither left a row.
- End to end: rules loaded from the database routed an invoice from a VIP address to the billing queue and the support department. The engine chose the queue's one-hour policy and skipped a support-only holiday, giving a target of Friday 10:00 London time.
- A rule inserted around validation (`regex` operator) loaded as invalid, produced `RULE_INVALID`, and **escalated** a conversation that was otherwise safe.
- SLA state: one row on first record, `BREACHED` with a timestamp, a later recompute **kept the first breach time**, and `MET` cleared it, which satisfies the C04 state/timestamp constraint.
- Deleting an organization removed all of its rules, VIP entries, and holidays.

**Constraints**

| Rejected, on the named constraint | Positive control accepted |
|---|---|
| `ck_routing_rules_a_rule_must_do_something` (empty actions) | valid rule row |
| `ck_routing_rules_actions_are_an_object`, `ck_routing_rules_conditions_are_an_object` | |
| `ck_routing_rules_priority_is_bounded` (−1 and 10001) | priority 10000 |
| `ck_routing_rules_version_starts_at_one`, `ck_routing_rules_effective_window_is_ordered`, `ck_routing_rules_name_not_blank` | |
| `uq_routing_rules_name_version` | same name and version in another organization |
| `ck_vip_contacts_exactly_one_of_address_or_domain` (both, and neither) | |
| `ck_vip_contacts_address_is_lowercase`, `ck_vip_contacts_domain_is_lowercase` | |
| `uq_vip_contacts_address`, `uq_vip_contacts_domain` | same domain in another organization |
| `uq_business_holidays_organization` (a repeated organization-wide holiday, where `department_id` is NULL) | organization-wide holiday; two departments on the same date |
| `uq_business_holidays_department`, `ck_business_holidays_name_not_blank` | |

**Migration reversibility:** downgrading to `20260915_0006` removed all three tables, and re-upgrading restored them at `20260916_0007`.

**Incidental confirmation:** the database session ran in this machine's zone (Asia/Kolkata), not UTC or London. Every timestamp came back in that zone, and every target was still correct, because nothing in the path depends on the server's zone.

## Residual risks and open items

| Item | Status |
|---|---|
| The client's actual rules, departments, VIP list, holidays, working hours, and zone | **Required from the client** before the pilot; the engine refuses to guess |
| No administration screen for rules yet | Rules are managed through `PostgresRuleStore`; an admin interface belongs with C13/C14 hardening or can be pulled forward |
| The engine is not yet called from the live triage pipeline | Integration is C11's migrated pipeline; the engine is built to be called there with the evidence the graph already produces |
| Guardrails are the MVP's keyword rules, unchanged | Deliberate (preserved, not reimplemented). Their recall limits carry over; model-assisted risk detection may only add codes, per this phase's invariant |
| Breach detection is computed when called, not scheduled | A periodic sweep belongs with the worker deployment (C07 carry-over) |
| The full pgvector chain has not been applied since `0006` | `0007` does not touch pgvector; re-apply when Docker is available |
| Nine phases remain uncommitted | Recommended before C10 |
