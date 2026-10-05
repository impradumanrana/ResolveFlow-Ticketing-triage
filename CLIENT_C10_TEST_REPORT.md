# ResolveFlow AI - C10 Verification Report

**Phase:** C10 - Client BYOK AI gateway

**Result:** GREEN

**Date:** 17 September 2026

**Gate:** "Invalid, expired, rate-limited, budget, invalid-schema, and fallback cases fail safely and visibly."

## Scope and safety

**No paid model was called, and no request reached OpenAI.** Every provider behaviour was produced by a scripted provider, or by the real pinned OpenAI SDK talking to a simulated server over its own HTTP layer. No cloud resource was created, no mailbox was connected, and no production data was touched. The client's provider, models, region, budget, and key remain unsupplied; nothing here can run until they are.

Docker was still unavailable, so the rendered migration chain was applied to a throwaway PostgreSQL 17 in the session scratchpad with **exactly three** pgvector statements excluded, each asserted to occur once. Migration `0008` does not touch pgvector. The database, both local servers, and the browser artifacts were removed afterwards.

## Delivered

- **`app/gateway/`** - one contract for every provider:
  - `policy.py` - approvals, candidate routing, conservative cost estimates.
  - `credentials.py` - Secret Manager over REST, a `SecretValue` that refuses to print itself, a short-lived cache.
  - `openai_adapter.py` - the launch provider through the pinned SDK, its own retries off, its regional endpoints, errors reduced to codes.
  - `service.py` - policy, budget, credential, call, structure, fallback, in that order.
  - `store.py` / `memory_store.py` - PostgreSQL and offline stores.
  - `mvp_provider.py` - the proven MVP prompts, running through the gateway.
- **Migration `20260916_0008`** - `ai_model_approvals`, `provider_budget_ledgers`, `provider_calls`, plus failure-visibility columns and a check that `ai_configs.credential_secret_name` can hold only a Secret Manager resource name.
- **Internal API** - `GET /v1/ai/settings`, `POST /v1/ai/providers/{provider}/verify`, `PUT /v1/ai/providers/{provider}/credential`; contract regenerated.
- **Workspace page** - `/workspace/settings/ai`: status, masked key, region, models, fallback, approved models, budget meter, recent problems in plain words, and - for Owners and Administrators - check and replace.
- **Infrastructure** - an add-only `secretVersionAdder` grant on the one BYOK secret, a posture check that no other secret may be written by a runtime, and runbook procedures.

## How each gate case fails safely and visibly

| Case | Safely | Visibly |
|---|---|---|
| **Invalid key** | No retry on the same key, no fallback, no spend | `CREDENTIAL_INVALID`, a `FAILED` call row, provider shown as *Failing* with "The provider rejected the API key." |
| **Expired key** (version disabled or destroyed) | Refused before any provider call; no spend | `CREDENTIAL_EXPIRED`, a `REFUSED` row, same treatment on the page |
| **Rate limited** | One retry honouring `Retry-After`, capped at 10s, then an approved fallback in the same region | `RATE_LIMITED` attempts recorded; a successful fallback is reported as `PROVIDER_FALLBACK_USED` and counted for the operator |
| **Budget** | Worst-case cost reserved atomically *before* the call; no budget means no calls at all | `BUDGET_EXCEEDED` / `BUDGET_NOT_CONFIGURED`, a `REFUSED` row, a budget meter that warns at 80% and says "paused" when exhausted |
| **Invalid schema** | Exactly one repair, then a person; never a fallback and never an unvalidated answer | `INVALID_SCHEMA` with the failed attempt still billed and shown |
| **Fallback** | Only for availability failures, only to approved models, only in the same region, never for embeddings | Every rejected candidate is recorded with the reason (`REGION_NOT_SUPPORTED`, `MODEL_NOT_APPROVED`) |

In the triage pipeline every one of these reaches the person as `MODEL_ERROR`, which C09's engine routes to a human, with the draft withheld.

## Automated gate evidence

| Check | Result |
|---|---|
| `make check` | **exit 0** |
| Python suite | **1,248 passed** (1,016 at C09) |
| Web suite | **146 passed** (126 at C09) |
| New: gate cases end to end | 87 tests |
| New: the real SDK, Secret Manager, key hygiene | 67 tests |
| New: settings, roles, MVP pipeline | 43 tests |
| New: internal API | 24 tests |
| New: schema invariants for `0008` | 5 tests |
| New: infrastructure posture | 4 tests |
| Ruff, mypy (`app/gateway` added), ESLint, TypeScript, Next.js build, infrastructure checks | clean |

### Mutation check

Twenty-nine deliberate defects were injected one at a time and the gate suites run against each.

**29/29 caught**, including: chaining the SDK error, letting the SDK retry, allowing fallback after quota exhaustion or a credential failure, removing the budget check, not charging a timeout, allowing embedding fallback, allowing cross-region fallback, using an unapproved fallback, leaving a rejected key in the cache, storing a key before checking it, granting Supervisors management, echoing input in validation errors, billing missing usage as free, settling twice, mapping a disabled secret to the wrong code, waiting out a long provider delay, dropping the structure from a repair, ignoring the approval's output cap, making `SecretValue` printable, repairing truncated output, and treating a missing budget as unlimited.

One mutant (`organization=None` while the `Omit` headers remained) was equivalent at the HTTP boundary. Rather than leave it, a test now pins the client's own attributes, because the SDK copies them into derived clients.

## Defects found during the phase

1. **A budget reservation would have locked the ledger row for the length of a model call.** The store originally ran inside the request transaction, so a 60-second generation would serialise every AI call for that provider and hold a database connection throughout. Each store operation now runs in its own short transaction. A live test proves it: with six calls in flight, `SELECT ... FOR UPDATE NOWAIT` on the ledger succeeds and no connection is idle in a transaction.
2. **Three exception chains carried secrets.** `raise ... from None` hides the original from a printed traceback but leaves it attached as `__context__`, reachable by any error reporter. The attached objects were: OpenAI's `AuthenticationError`, whose message quotes part of the key; an `httpx` error, which holds the request and its bearer token; and a `UnicodeDecodeError`, which holds the raw secret bytes. All three are gone, and an AST test now refuses any new exception raised inside an `except` block in `app/gateway`.
3. **The same flaw existed in C06's Google client** and is fixed there too: its `httpx` errors carried requests holding the Gmail bearer token, the refresh token, and the client secret.
4. **The SDK inherits credentials from the environment.** `organization=None` makes it read `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, and `OPENAI_ADMIN_KEY`, which could bill a different OpenAI organization. The adapter now passes empty values and omits the headers.
5. **Validation errors repeated the rejected input.** FastAPI's default handler echoes it, and these bodies carry API keys and OAuth codes. A handler now returns only the location and the kind of problem - which also closes the same hole for C06's callback.
6. **A rejected key was put back in the cache.** The rotation check re-read the secret; when the key had not changed, the re-read re-cached the rejected key. It is evicted again before the failure is raised.
7. **A passing check left the provider marked "Failing".** A successful verification or replacement now clears exactly the failures it proves fixed - credential, permission, model, region - and deliberately leaves budget, quota, and rate-limit failures visible.
8. **A working day ending at midnight and a stale `0007` head** were carried in from C09's calculator work and corrected there.

## Live PostgreSQL evidence

Chain applied to `20260916_0008`; **71/71 checks passed**, each negative check failing on its *named* constraint, each group with a positive control.

- Metering is exact: a call reserving 160,800 micro-units and costing 24,000 settles to `(spent 24,000, reserved 0)`; 42 calls roll 1,008,000 micro-units into 1 minor unit.
- A repair is two attempts (`CALL` failed `INVALID_SCHEMA`, `REPAIR` succeeded); a fallback records the rejected attempts and `fallback_from`.
- Exactly 35 calls fit a one-unit budget, and the 36th is refused before it is made.
- Twenty concurrent calls against a budget holding six reservations: exactly six ran, fourteen were refused, and the ledger settled to exactly six charges.
- A crashed worker's reservation is released after 15 minutes and **charged**, because the provider may have done the work; releasing again does nothing; settling twice moves money once.
- Verification, key replacement, and refusals write audit events with `target_type = 'ai_provider'`; a denied view is audited as `DENIED`.
- A passing verification clears a stored `CREDENTIAL_INVALID` and deliberately leaves `BUDGET_EXCEEDED` in place.
- Recent problems come back with their own messages, and a successful fallback is counted for the operator.
- No key, and no fragment of one, appears anywhere in the database.
- Downgrade to `0007` removes all three tables and all seven added columns; re-upgrade restores them.

## Browser evidence

Driven with Playwright against a local dev server, a scratch database, and the simulated provider, signed in as three different people.

| Checked | Result |
|---|---|
| Owner sees the page | Status *Failing* with "The stored API key version is disabled or destroyed.", masked key `••••0000`, region, models, fallback, approved models, budget meter at 84% "Nearly used up." |
| "Check the stored key" | Status became *Working*, "The stored key works for 4 models", verified time updated |
| A key the provider rejects | "The provider rejected the API key. Nothing was changed."; stored last four unchanged; audit row `FAILED` |
| A working key | "The new key works for 4 models and has been stored."; hint became `••••PqRs`; audit metadata carries only the mask |
| The key in the page | Field cleared after submit, `type="password"`, `autocomplete="off"`; the full key appears nowhere in the DOM |
| Recent problems | Listed in the gateway's own words with counts; "A fallback model answered 2 times because the first choice was unavailable." |
| Supervisor | Sees the masked view; no check or replace controls; told why |
| Agent | No navigation link; the direct URL is refused and recorded |
| Phone width (390px) | No horizontal scrolling, nothing overflows, 40px controls - the workspace standard, above WCAG 2.2's 24px |
| Console | No errors or warnings |

Two notes on method. The first sign-in used `/signin`, which began a redirect to Google's public sign-in page with a dummy client id; no credential was sent, and later sign-ins used a neutral page. The "expired key" state on the page was written directly to the database after the seeding order left the key cached; the refusal path itself is proven offline and against PostgreSQL.

## Residual risks and open items

| Item | Status |
|---|---|
| Provider, model, region, budget, prices, and the key itself | **Required from the client.** Until then the gateway refuses every call, which is the intended state |
| The gateway is not yet wired into the running triage API | `GatewayProvider` is proven against the real graph; the switch belongs with C11's migrated pipeline, where `provider.rule_codes` should also reach the triage result |
| Only OpenAI has an adapter | The contract is provider-agnostic and a second was not built, because the client has approved one provider. Cross-provider fallback is tested with a simulated second provider |
| Budget is per provider per calendar month, in UTC | Deliberate and documented. A client whose billing month differs will see a boundary difference |
| Stale reservations are released only when something calls the sweeper | Belongs with the worker deployment, alongside C07's other scheduled work |
| Error reporters that capture frame locals could still see a key | Not a code defect: any such tool must be configured without local-variable capture. Belongs in C13 hardening |
| Key replacement is Owner/Administrator only, audited, but not rate limited | Worth a limit in C13 |
| The full pgvector chain has not been applied since `0006` | `0008` does not touch pgvector; re-apply when Docker is available |
| Ten phases remain uncommitted | Recommended before C11 |
