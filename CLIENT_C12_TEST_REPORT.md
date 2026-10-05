# ResolveFlow AI - C12 Verification Report

**Phase:** C12 - Human review and provider drafts

**Result:** GREEN

**Date:** 5 October 2026

**Gate:** "No path auto-sends; concurrency and duplicate-action tests pass; every decision is attributable."

## Scope and safety

**Nothing was sent, and nothing can be.** An approval creates a *draft* in the client's mailbox for a person to send; there is no send call in the codebase, and three independent layers keep it that way:

* `organizations.sending_enabled` is constrained to `false` by the C03 schema.
* C06 connects mailboxes with `gmail.readonly`, its scope policy refuses any write-capable scope, and **its migration adds a database constraint that refuses to store a credential carrying `gmail.compose`**. So a provider draft is not merely disabled by configuration - it is impossible until a migration, reviewed and approved by the client, changes that constraint.
* No file in `app/`, `scripts/` or `apps/web/src` references a send endpoint, asserted by a test that strips comments and docstrings first.

No paid model was called, no cloud resource created, and no real mailbox connected. Live verification used a throwaway PostgreSQL 17 in the session scratchpad with the usual three pgvector statements excluded; the database, both local servers, and the browser artifacts were removed afterwards.

## Delivered

- **`app/review/`** - one service for every human decision:
  - `records.py` - the six decisions, the request that names the version it was made against, and the outcome.
  - `access.py` - the permission each decision needs, mirroring the web role matrix.
  - `service.py` - replay, visibility, permission, validation, the change under optimistic locking, then the provider draft.
  - `preview.py` - what the draft would contain, and the MIME built from exactly that.
  - `drafts.py` - the one provider call (`POST /drafts`), with a refusing composer for a read-only mailbox.
  - `store.py` / `memory_store.py` - PostgreSQL and offline stores.
- **Migration `20260917_0009`** - `draft_revisions` (the model's answer is revision 1 and is never overwritten; a human edit is a new revision with its author) and `provider_drafts` (claimed before the provider is called, one live draft per conversation, and no column that could represent a send).
- **Internal API** - `POST /v1/tickets/{id}/review` and `POST /v1/tickets/{id}/draft-preview`; contract regenerated.
- **Workspace** - the ticket page now offers Save, Approve, Reject, Move, Assign and Mark resolved to whoever holds each permission, with a preview of what approving would create and a plain statement of what it does.

## The gate

### No path auto-sends

| Layer | Evidence |
|---|---|
| Code | A test strips comments and docstrings from every Python and TypeScript source, then looks for `messages/send`, `drafts/send` and `sendMessage`. A companion test proves the scan fails on a real send call and passes on prose about one |
| The composer | `GmailDraftComposer` has exactly one public method, `create`. Mutating its endpoint to `messages/send` is caught |
| Schema | A column allowlist refuses anything matching `sent`/`sending` outside the two legitimate places (a received message's `sent_at`, and the organization flag constrained to false) |
| Credentials | A database constraint refuses to store any send-capable scope. Originally `ck_mailbox_credentials_no_write_scope_in_v1`, which also refused compose; see the addendum below |
| Contract | The published decision enum is exactly the six decisions; "SEND" appears nowhere in the API |
| Behaviour | Approving on this deployment records the decision and reports `DRAFT_SCOPE_NOT_GRANTED`. The conversation stays with a person |

### Concurrency

Eight threads approved one conversation at the same version against real PostgreSQL: **one applied, seven saw `VERSION_CONFLICT`**, exactly one provider draft was created, exactly one live draft row existed, and all seven losing attempts were recorded as refused actions. The same test offline, against the in-memory store, gives the same answer.

A stale view is reported as a conflict *before* any other refusal, because every other refusal could be an artefact of a view that has since changed, and "reload and decide again" is the only answer that helps.

### Duplicate actions

One rendered form is one decision: the key is derived from the decision pressed and a nonce minted with the page. A second submission replays the first outcome - including its provider-draft result - without moving the ticket again, without a second action row, and without calling the provider twice. Verified offline and live.

### Every decision is attributable

Each decision records the actor, the reason where a reason is owed, the ticket version before and after, an idempotency key, and an audit event. **Refused decisions are recorded too** - wrong role, missing reason, stale version - as `REJECTED` actions with their code and a `DENIED` audit event. A provider draft is its own action and names the person who approved it; the column is `NOT NULL`.

## Automated gate evidence

| Check | Result |
|---|---|
| `make check` | **exit 0** |
| Python suite | **1,491 passed** (1,344 at C11) |
| Web suite | **181 passed** (158 at C11) |
| New: the review service | 60 tests |
| New: the draft and its preview | 37 tests |
| New: what a decision writes | 24 tests |
| New: the review endpoints | 22 tests |
| New: the workspace controls | 23 web tests |
| New: schema invariants for `0009` | 4 tests |
| Ruff, mypy (`app/review` added), ESLint, TypeScript, Next.js build, infrastructure checks | clean |

### Mutation check

Twenty-four deliberate defects, injected one at a time: a send endpoint in the code, a read-only mailbox given a real composer, the optimistic lock dropped, a lost lock retried instead of reported, the stale-version check removed, a replay applied again, acting on view permission alone, the department check neutered, read-only roles allowed to decide, the per-decision permission ignored, an ungrounded draft approved as it stands, a reason no longer required, a refusal not recorded, an edit overwriting the model's revision, a human revision recorded as the model's, the draft created before it was claimed, the claim using stale facts, the capability check moved after the preview, a draft addressed to a guess, a header keeping its newlines, a stacked "Re:", a provider error treated as success, a schema column that could record a send, and a human revision without an author.

**24/24 caught.** Three survived the first pass and each exposed a real gap: the visibility predicate could be neutered without being deleted (now asserted to contain no comment or tautology), the revision number was only exercised for a first edit (now asserted for a second), and - the important one - **the in-memory store was more forgiving than the database**, repairing a caller that passed the wrong revision. That is exactly what had hidden the defect below, so the offline store now falls back the same way the real one does.

## Defects found during the phase

1. **Approving recorded an edit.** The shared form had a hidden `decision` field *and* a submit button of the same name; both post a value and the first wins. Found by clicking Approve in a browser and reading the action row: `DRAFT_EDITED`. The button now names the decision, and the idempotency key is derived from the decision pressed - which also fixes a second latent bug, where Save and then Approve from one rendered form would have shared a key and the approval would have replayed the edit.
2. **The draft claim used facts loaded before the decision.** Approving with edited text on a conversation with no prior revision wrote revision 1 in the decision and then tried to write revision 1 again. Found live, because the in-memory store silently repaired it. The revision the decision produced is now passed explicitly, and both stores fall back identically.
3. **The mailbox address was looked up by duck typing.** `getattr(store, "mailbox_address", None)` found nothing on the PostgreSQL store, so every approval reported `MAILBOX_ADDRESS_INVALID` instead of the true reason. The address now travels in the ticket facts, read in the same query.
4. **A read-only mailbox was told the wrong reason.** The preview was built before the capability check, so a missing customer address masked the scope refusal. The capability is checked first: a mailbox that cannot hold a draft cannot hold one whatever the reply looks like.
5. **The reply box pre-filled internal text.** On an escalated conversation the box contained "Human review required… Triggered controls: HIGH_URGENCY, ANGRY_CUSTOMER…". The service would refuse approving it unchanged, but a box pre-filled with internal codes invites someone to tweak a word and send them. Only a verified customer answer pre-fills now; otherwise the box is empty and says that anything written there is the person's own.
6. **A dead assertion in my own test.** `api[1].actions == []` as a bare statement asserts nothing; Ruff's B015 caught it.

## Live PostgreSQL evidence

Chain applied to `20260917_0009`; **62/62 checks passed**, each negative check failing on its *named* constraint, each group with a positive control.

- A decision records the actor, the reason, both versions, an audit event, and moves the conversation.
- A human edit is its own revision with its author and its digest; the model's own draft in `triage_runs` is untouched.
- Approving on a read-only mailbox records the approval, a `REFUSED` provider draft with `DRAFT_SCOPE_NOT_GRANTED`, and its own `PROVIDER_DRAFT_CREATED` action; a replay returns the same provider outcome.
- With a compose-scoped credential (the C06 constraint dropped inside the test, to prove the path works when the client authorizes it) an approval creates a draft, points it at the revision it was built from, and records the text's digest.
- Eight concurrent approvals: one applied, seven conflicts, one provider call, one live draft row, seven refusals recorded.
- A duplicate submission replays: no second provider call, one action for the key, the ticket moved once.
- A mailbox you may only *view* is not actionable; a department you do not belong to is not found; joining it makes it actionable; nothing is written for an invisible attempt.
- A read-only role is refused, recorded, and audited as denied.
- An ungrounded candidate cannot be approved as it stands; a person may approve their own words, stored as theirs.
- A stale claim is closed once as `DRAFT_OUTCOME_UNKNOWN`, and not twice.
- The database refused: a model revision claiming an author, a human revision without one, the model writing a revision other than 1, a duplicate revision number, a blank body, a digest that is not a SHA-256, an unknown source, a created draft without a provider id, an unsuccessful draft without a code, provider text as a code, a settled timestamp on a pending draft, a second live draft for one conversation, a duplicate idempotency key, an unknown status, a draft without an approver, and deleting a revision a draft points at.
- Another organization saw no decisions and no revisions; no credential material appears in anything review wrote.
- `0009` downgrades and re-upgrades cleanly.

## Browser evidence

Driven with Playwright against a local dev server and a scratch database seeded by running the C11 pipeline over four conversations.

| Checked | Result |
|---|---|
| Owner, answerable conversation | All six controls, the preview disclosure, and "Approving records your decision. No draft is created: support@acme.example is connected read-only." |
| Approve | "Recorded. No provider draft was created: this mailbox is connected read-only. The decision is recorded and the conversation is with a person."; `DRAFT_APPROVED` and a failed `PROVIDER_DRAFT_CREATED` in the database; conversation moved to `IN_PROGRESS` |
| Another reviewer acting first | "Someone else changed this conversation first. Reload and decide again." with a reload link; the conversation unchanged; the attempt recorded as `TICKET_RESOLVED` / `REJECTED` / `VERSION_CONFLICT` |
| Agent | Save, Approve, Reject, Mark resolved - no Move, no Assign |
| Supervisor | Those, plus Move (queue and department) and Assign |
| Escalated conversation | The reply box is empty, with "There is no verified answer for this conversation. Anything you write here is your own, recorded as yours." |
| Nothing sent | No draft row reached `CREATED`; `sending_enabled` still false |
| Console | No errors or warnings |

## Residual risks and open items

| Item | Status |
|---|---|
| Creating a real provider draft needs a scope change | **Resolved 2026-10-05 at the client's instruction.** The capability and the procedure are now in place; see the addendum below. Still unexercised against real Google |
| A draft whose outcome is unknown | A claim whose worker died is closed as `DRAFT_OUTCOME_UNKNOWN` by a sweeper that something must call; until the worker is deployed, run it from the command line. The operator is told to check the mailbox before approving again |
| No bulk review | Deliberate: approving in bulk is how an unverified answer reaches a customer. Assignment in bulk already exists from C08 |
| Approval does not notify anyone | There is no notification surface yet; the draft sits in the mailbox and the conversation shows its state |
| The reviewer's own words are not grounding-checked | By design (C-D117), and recorded as theirs. A validator for human text would be a different product decision |
| Rate limiting of decisions | Not implemented; belongs with C13's hardening alongside the other limits |
| Twelve phases remain uncommitted | Recommended before C13 |


---

# Addendum - the compose scope change (2026-10-05)

The client authorized adding `gmail.compose` to the consent request and storing
a compose-scoped credential, which is what the gate above was waiting for. The
capability is built, tested, and verified against live PostgreSQL. **No real
mailbox has been re-consented** - that needs the client's Workspace
administrator acting on their own OAuth client, and the procedure is written
down rather than performed.

## What changed

| Piece | Change |
|---|---|
| `app/mailbox/scopes.py` | Two scope profiles. `read_only` is the default and byte-for-byte the previous behaviour; `read_and_draft` adds `gmail.compose` as an **optional** scope. Selected by `RESOLVEFLOW_MAILBOX_SCOPES`; anything unreadable resolves to `read_only` |
| `migrations/versions/20260918_0010_compose_scope.py` | `no_write_scope_in_v1` narrowed to `no_send_capable_scope`: compose storable, the seven send-capable scopes still refused. The narrower constraint is added **before** the broader is dropped |
| `app/review/tokens.py` | `RefreshingTokenProvider` leases a 45-minute access token from C06's `ConnectionService.refresh`. The sealed refresh token is never handled here, the cached token is unprintable, and the refresh runs in its own short transaction that closes before the Gmail call |
| `app/api/review.py` | `_drafting_dependencies` supplies a transport and token provider only when the drafting profile is active *and* the OAuth client is configured *and* the vault opens. Otherwise the review service keeps refusing, with no flag to remember |
| `app/review/service.py` | A transport with no token provider now refuses with `MAILBOX_TOKEN_UNAVAILABLE` instead of calling Gmail with an empty bearer and reporting the resulting 401 as the mailbox's fault |
| `infra/terraform/.../environment` | `mailbox_scope_profile`, defaulting to `read_only`, validated to the two values, surfaced as `RESOLVEFLOW_MAILBOX_SCOPES` beside `RESOLVEFLOW_SENDING_ENABLED` |

## What did not change

Every no-send guarantee. `gmail.compose` permits sending at Google's end, so
the prohibition is structural rather than scope-shaped, and all six layers in
"No path auto-sends" above still hold - including the credential row, which now
refuses `gmail.send`, `gmail.modify`, `gmail.insert`, `gmail.labels`, both
`gmail.settings` scopes and `https://mail.google.com/` under **both** profiles.

The read-only deployment is unchanged: same consent request, same refusals,
compose included. The 326 mailbox, review, schema and API tests that existed
before this change all still pass; the three that referred to the old
constraint by name were rewritten to assert the same guarantee against the
rendered schema and the new migration, so they now fail if sending ever becomes
storable.

## Compose is optional

Google's granular consent lets a person untick one box. A grant missing
`gmail.compose` connects the mailbox read-only and turns drafting off; a grant
missing `gmail.readonly` is still a refusal. The same asymmetry holds at every
token refresh: compose disappearing later leaves the mailbox connected, while
the mailbox scope disappearing withdraws access and destroys the credential.

## Automated evidence

| Check | Result |
|---|---|
| `make check` | **exit 0** |
| Python tests | **1,584 passed** (1,491 before; +93) |
| Web tests | **181 passed** |
| `ruff`, `mypy` | clean on the delivered surface |
| `scripts/validate_infra.py` | 63 files across 13 modules |
| New test modules | `tests/test_mailbox_scope_profiles.py` (53), `tests/test_review_tokens.py` (32); the rest are additions to the existing mailbox, review, schema and infra modules |

### Mutation check

29 mutants, **28 caught**. The survivor is an equivalent mutant: changing the
OAuth configuration guard from `and` to `or` produces the identical outcome
because `OAuthClientConfig.__post_init__` raises `ValueError` on any empty
field and the same handler returns `None`. Two earlier survivors were real test
gaps and were fixed: a test asserting on source text rather than behaviour, and
an uncovered branch where a token provider returns an empty string.

Mutants caught include: the default profile becoming `read_and_draft`; an
unreadable setting widening access; compose becoming required rather than
optional; excess scopes never being refused; `gmail.send` landing on the
permitted list; the migration dropping the old constraint before adding the
new one; a cached token never expiring; one token being shared across
mailboxes; a stale token surviving a failed refresh; an empty bearer reaching
Gmail; and the read-only deployment being wired for drafting.

## Defects found during this change

1. **`drafting_available` answered "no" for Google's own scope string.** It
   normalised through `list(granted)`, which splits a string into characters.
   No caller passed a string, so nothing was broken - but the function accepts
   the same shapes as `evaluate_granted_scopes`, so the first caller that did
   would have been told a compose grant could not draft. Fixed and pinned by
   five cases.
2. **A transport with no token provider called Gmail with an empty bearer.**
   The resulting 401 would have been reported as the mailbox's fault. Now
   refused as `MAILBOX_TOKEN_UNAVAILABLE` before any call is made.
3. **Two tests that could not fail.** One asserted on the source text of
   `_compose` rather than its behaviour; one covered a branch that a second
   guard also caught. Both found by mutation testing and replaced with
   behavioural tests.
4. **Three tests named the old constraint.** They passed because migration 0006
   still contains it, while the live schema no longer does. Rewritten to read
   the rendered chain and the new migration's computed constraint.

## Live PostgreSQL evidence

Docker is still unavailable, so a throwaway Homebrew PostgreSQL 17.10 was used
with exactly three pgvector statements excluded (each asserted to occur once),
and torn down afterwards.

| Check | Result |
|---|---|
| Chain applies to `20260918_0010` | yes; `mailbox_credentials` carries `ck_mailbox_credentials_no_send_capable_scope` and no longer carries `ck_mailbox_credentials_no_write_scope_in_v1` |
| Credential rows: permitted | readonly+identity **stored**; readonly+identity+compose **stored** |
| Credential rows: refused | `gmail.send`, `gmail.modify`, `gmail.insert`, `gmail.labels`, `gmail.settings.basic`, `gmail.settings.sharing`, `https://mail.google.com/`, send alone, compose+send - **all 9 refused by name** |
| Negative control | the same compose row against the schema as it stood at `20260917_0009` was refused by `ck_mailbox_credentials_no_write_scope_in_v1`, so the migration is what changed the answer |
| Downgrade with a compose credential present | **refused**, transactionally: constraint, credential and revision all unchanged |
| Downgrade after disconnecting | clean, and C06's constraint restored |
| Scopes reach the review path | `granted_scopes` round-trips the compose URI through the exact join `load_ticket` uses, and `composer_for` returns a real `GmailDraftComposer` |

## End-to-end, against a simulated Google

One test runs the whole chain with nothing stubbed but Google itself: consent
with compose through C06's simulated OAuth, the credential sealed and stored,
a token leased through `ConnectionService.refresh`, and an approval creating a
MIME draft at `POST /gmail/v1/users/me/drafts` with a live bearer - addressed
to the customer, subject `Re: ...`, threaded. A second test runs the same
wiring against a mailbox connected read-only: the approval stands, no draft is
created, no Gmail call is made.

## Residual risks

| Item | Status |
|---|---|
| No mailbox has been re-consented | The capability is unexercised against real Google. It needs the client's Workspace administrator to add the scope to their consent screen and reconnect the mailbox. `docs/MAILBOX_CONNECTION_MODES.md` and `infra/docs/RUNBOOK.md` carry the steps |
| `gmail.compose` permits sending at Google's end | Unavoidable: Gmail has no narrower draft scope. Mitigated structurally, in the four places listed above, not by the scope |
| A person may untick compose | Supported: the mailbox connects read-only and drafting is off. Reconnect to grant it |
| The token provider's cache is per process | Correct, but it means two API instances each refresh once. Google's quota is ample for this; worth revisiting only if instance counts grow |
| Nothing sweeps stale draft claims yet | Unchanged from the main report: no worker is deployed, so the sweeper runs from the command line |
| Thirteen phases remain uncommitted | Recommended before C13 |
