# ResolveFlow AI - C07 Verification Report

**Phase:** C07 - Reliable multi-mailbox ingestion

**Result:** GREEN

**Date:** 16 September 2026

## Scope and safety

**No real mailbox was connected and no request reached Google.** Every scenario ran against a simulated Gmail that is strict where Gmail is strict: it answers 404 when a cursor predates its retained history, refuses unknown tokens, and returns only the history records after the requested cursor. No cloud resource was created, no paid model was called, and no production data was touched.

**Environment note.** The Docker daemon was not running, so the pgvector container was again unavailable and I did not relaunch Docker Desktop. Live verification used a throwaway PostgreSQL 17 in the session scratchpad, torn down afterwards. The real rendered migration chain was applied with **exactly three** pgvector statements excluded, each asserted to occur once, stopping on any other error. C07 adds no migration, so nothing new depends on that exclusion.

## Delivered

`app/ingestion/`:

- **`pubsub.py`** - push envelope parsing and vetting; the Pub/Sub message id is the deduplication key.
- **`history.py`** - folding history records into effects, with a monotonic cursor and deletion beating addition.
- **`normalize.py`** - Gmail resource to C04 rows, treating email as untrusted input.
- **`gmail.py`** - watch, history.list, messages.get, messages.list over the shared injectable transport, with classified failures.
- **`service.py`** - notifications, sync, reconciliation, watch renewal, job draining, restart recovery, and access-token caching.
- **`records.py`**, **`store.py`**, **`memory_store.py`** - the storage port, the PostgreSQL adapter, and the offline fixture.

**No new migration.** Ingestion writes into the C04 tables as designed, and uses the existing `jobs` table with its idempotency key for notification deduplication. That the schema needed no changes is the C04 design working.

## Automated gate evidence

| Check | Result |
|---|---|
| `make check` | **exit 0** |
| Python suite | **505 passed** (was 410 at C06) |
| Web suite | 77 passed (unchanged; C07 adds no web surface) |
| mypy | no issues in 37 source files |
| Ruff / ESLint / TypeScript | clean |
| Next.js production build | passed |
| Infrastructure checks | 0 findings |
| Ingestion suites | 95 passed offline |
| Live PostgreSQL verification | **33/33** |

## The gate, case by case

The C07 gate names seven recovery scenarios. Each was proven offline and again end to end through the real service and the real PostgreSQL store, one transaction per unit of work.

| Scenario | Offline | Live on PostgreSQL |
|---|---|---|
| **Duplicate** - notification redelivered | second is `DUPLICATE`, one job | one job row; replay adds nothing |
| **Duplicate** - same message in two history records | fetched once, ingested once | - |
| **Duplicate** - window reprocessed after a lost cursor write | 1 duplicate, 0 ingested, one row | cursor reset then resynced: 0 ingested, 2 duplicates, row count unchanged |
| **Delayed** - notification published before an earlier sync | cursor unchanged; never rewinds | SQL refuses a lower cursor outright |
| **Out of order** - newer notification processed first | every message ingested once; cursor is the maximum | - |
| **Expired watch** - near expiry, or never registered | renewed; expiry moves forward; cursor advances only forwards | renewed; expiry in the future |
| **Expired history cursor** - older than Gmail's retained window | reconciles by listing messages; a fresh watch supplies the new cursor | - |
| **Revoked token** | stops without calling Gmail; audited; job completes rather than retrying | - |
| **Revoked token** - Gmail rejects a cached token mid-sync | token dropped; next attempt asks for a fresh one | - |
| **Quota** - 429, and 403 with a quota reason | deferred with backoff; succeeds once clear | job `QUEUED`, attempts 1, deferred into the future |
| **Quota** - repeated failure | dead-lettered at the attempt limit | `DEAD_LETTERED` with a timestamp |
| **Restart** - worker dies holding a job | lease expires, job reclaimed, then runs | reclaimed and re-queued |
| **Restart** - two workers race for one job | - | **exactly one of two concurrent transactions wins** |

### Load: fifty mailboxes

The plan requires simulating fifty mailboxes before expanding live ones. Fifty mailboxes against a shared history stream of 100 messages: every mailbox ingests all 100, giving **5,000 messages and 5,000 tickets**, each exactly once. Replaying all fifty notifications afterwards produced **zero** additional rows and zero additional jobs. One revoked mailbox among three did not prevent the others syncing.

### Data handling

Verified live: ticket references assigned per organization by the C04 trigger; thread counters recomputed rather than incremented; a deletion marking the message while retaining the row; a customer reply reopening a RESOLVED ticket; an allowed attachment stored as `PENDING` for scanning while an executable is recorded as `SKIPPED` and never fetched; audit rows written for ingestion events.

Offline, on untrusted input: timestamps taken from Gmail's `internalDate` and not the forgeable `Date` header; CR and LF stripped from header-derived values; attachment filenames reduced to a basename (`../../etc/passwd` and `..\..\windows\system32\cmd.exe`); bodies, subjects, and recipient lists bounded; HTML-only bodies converted to text with script and style dropped; undecodable bodies handled without crashing.

### Constraints, each by name

| Rejected | Named constraint |
|---|---|
| The same provider message twice in one mailbox | `uq_messages_mailbox_provider_message` |
| The same notification queued twice | `uq_jobs_idempotency_key` |
| A second ticket on one thread | `uq_tickets_thread` |

Positive control: two different mailboxes receiving the same Gmail message id were both accepted, which is why the uniqueness is per mailbox.

## Defects the verification caught

1. **A load test that could not fail.** My fifty-mailbox test contained `assert sum(...) == ... or True`, which is true regardless - the same trap flagged in C05 and C06. Replaced with exact arithmetic (50 mailboxes x 100 messages = 5,000 rows, replay adds none) and a guard asserting no `or True` remains in the file.
2. **A quadratic lookup that would have hidden itself.** The in-memory store found a thread's ticket by scanning every ticket, making the fifty-mailbox simulation O(n²). Indexed by thread. A slow test would most likely have been "fixed" by shrinking the simulation.
3. **Leftover scaffolding.** A `... if False else None` line survived into a committed test. Removed and the test rewritten to assert something real.
4. **Three test expectations were wrong, not the code.** A malformed envelope returns `DATA_NOT_BASE64`, which is more precise than the `DATA_NOT_JSON` I expected; the service deduplicates message ids *before* fetching, so the meaningful assertion is one API call rather than a duplicate count; and the first retry delay is 60 seconds, not 30.
5. **A live script that measured the wrong job.** A job reclaimed by the restart test was still queued, so it consumed the injected rate-limit error and two checks passed against a job that had succeeded. The queue is now drained before the quota test. This is the second time a verification script has produced a confident but meaningless result (C06 defect 4); reading the detail column, not the PASS, is what caught both.
6. **Build wiring duplicated a mypy target,** producing "Duplicate module named app.ingestion" and failing the gate after everything else passed.

## What C07 deliberately did not do

- **Connected no real mailbox and sent no request to Google.**
- **Exposed no HTTP endpoint.** The ingestion service is complete and verified, but nothing yet receives a Pub/Sub push or a scheduler call. C02's Terraform already declares those paths (`/internal/gmail/notifications`, `/internal/gmail/renew-watches`, `/internal/gmail/reconcile`) on the worker service, which has no code container yet. Those endpoints authenticate by Cloud Run OIDC rather than the internal bearer token, so their auth model belongs with the worker's deployment rather than being invented here.
- **Added no migration.** The C04 schema was sufficient.
- **Downloaded no attachment.** Candidates are classified; fetching and scanning are C13.
- **Triaged nothing.** Tickets are created; classification, retrieval, and drafting are C11.
- **Wrote no per-user seen state.** Unseen is derived by comparing `seen_through_message_at` against the thread's latest message (C-D078).

## Residual risks

| Risk | Treatment |
|---|---|
| No HTTP surface receives notifications yet | First task of the worker deployment; the paths are already defined in C02 |
| The full pgvector chain has not been applied since `0006` | C07 adds no migration, so nothing new depends on it; re-apply when Docker is available |
| Real Gmail may differ from the simulation - error shapes, history pagination, label behaviour | Verify against the client's test mailbox during the C14 pilot before any production mailbox |
| The load simulation shares one history stream, so it tests throughput and idempotency rather than per-mailbox isolation at scale | Per-mailbox streams and real quota behaviour belong to the staging pilot |
| The access-token cache lives on the service instance; each transaction in the live harness built its own | Correct in production, where one worker holds one service across jobs. Worth confirming under the real worker |
| Reconciliation lists 14 days on every run | Fine at one or two mailboxes; at fifty it deserves a narrower window or a cursor of its own |
| A ticket reopens on any inbound message | Deliberate minimum; C09 owns routing, reassignment, and SLA restart |

## Preserved working-tree state

`ResolveFlow-AI-Presentation.pptx` remained deleted and untouched. The untracked planning documents, local `.env`, and `app/data` were not modified. The MVP core and its tests are unmodified. The scratchpad PostgreSQL was stopped and its data directory removed.

## Gate conclusion

C07 meets its gate: duplicate, delayed, out-of-order, expired-watch, revoked-token, quota, and restart scenarios all recover correctly, proven offline and against real PostgreSQL, with fifty mailboxes simulated and a full replay adding nothing. C08 may begin.
