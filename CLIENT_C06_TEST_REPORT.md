# ResolveFlow AI - C06 Verification Report

**Phase:** C06 - Gmail connection and permissions

**Result:** GREEN

**Date:** 15 September 2026

## Scope and safety

**No real mailbox was connected, and no request reached Google.** Every connection, refresh, and revocation ran against a simulated Google that is strict where Google is strict: it verifies the PKCE verifier against the challenge from the authorization URL, refuses revoked and unknown tokens, and answers the Gmail profile call only for live access tokens. No cloud resource was created, no paid model was called, and no production data was touched.

**Environment note.** The Docker daemon was not running during this phase, so the pgvector container used in C04 and C05 was unavailable. I did not relaunch Docker Desktop. Live database verification instead used a throwaway PostgreSQL 17 in the session scratchpad, on its own port, torn down afterwards. That host has no pgvector, so the real rendered migration chain was applied with **exactly three** pgvector statements excluded - the extension, the embedding column, and the HNSW index - each asserted to occur once, with the apply stopping on any other error. All mailbox tables and constraints are therefore the real ones. The full chain including pgvector has not been re-applied since `0006` was added; see residual risks.

## Delivered

- **`app/mailbox/`**
  - `scopes.py` - read-only scope policy, checked for missing *and* excess scopes.
  - `vault.py` - AES-256-GCM keyring; ciphertext bound to organization, mailbox, and purpose; key ids for rotation.
  - `google.py` - OAuth code exchange, refresh, revocation, and Gmail profile over an injectable transport; no Google SDK.
  - `connection.py` - start, complete, refresh, revoke; reconnect is start + complete.
  - `access.py` - mailbox view/act/connect/revoke decisions.
  - `records.py`, `store.py`, `memory_store.py` - the storage port, the PostgreSQL adapter, and the offline fixture.
- **Migration `20260915_0006`** - `mailbox_credentials` and `mailbox_connection_attempts`.
- **Internal API** - start, complete, and revoke endpoints under `/v1/mailboxes`; contract regenerated.
- **Web routes** - `POST /api/mailboxes/{id}/connect`, `GET /api/mailboxes/oauth/callback`, `DELETE /api/mailboxes/{id}/connection`, with redirect, reflection, and origin guards.
- **`docs/MAILBOX_CONNECTION_MODES.md`** - shared mailboxes, individual mailboxes, Groups, aliases, and domain-wide delegation, written for the client's Workspace administrator.
- **Runbook** - keyring creation and a rotation procedure that cannot lock anyone out.
- **Terraform** - the `api` service may now read the Gmail OAuth client secret, and receives the Gmail client id and redirect; the web tier still holds neither.

## Automated gate evidence

| Check | Result |
|---|---|
| `make check` | **exit 0** |
| Python suite | **410 passed** (was 246 at C05) |
| Web suite | **77 passed** (was 62) |
| mypy | no issues in 28 source files |
| Ruff / ESLint / TypeScript | clean |
| Next.js production build | passed |
| OpenAPI contract | regenerated; 3 mailbox paths; none can send, draft, or modify |
| Terraform | `fmt` clean; staging and production `validate` pass; offline checks 0 findings |
| Migration chain | single head `20260915_0006`; real Alembic downgrade and re-upgrade of `0006` both exit 0 |

## The gate, case by case

The C06 gate names six behaviours. Each was proven offline against the simulated Google, and again end to end through the real service and the real PostgreSQL store, one database transaction per request as production does.

### Connect

| Case | Offline | Live |
|---|---|---|
| Owner or Admin connects a shared mailbox | pass | CONNECTED; exactly one credential row; attempt consumed with final outcome |
| Credential sealed; reference names the key | pass | envelope sealed; `credential_secret_name` = `vault:mailbox-token-encryption-key:<key-id>` |
| No token stored anywhere | pass | no issued token appears in any row of credentials, attempts, audit, or mailboxes |
| URL requests offline, read-only, S256 PKCE, no merged scopes | pass | - |
| Supervisor, Agent, Knowledge Manager, Auditor cannot start | pass | - |
| Groups, aliases, off-domain, other-organization mailboxes refused before OAuth | pass | - |
| Callback replayed | `ATTEMPT_REPLAYED`, one token exchange | same |
| Replay presented *after* expiry | still `ATTEMPT_REPLAYED`, audited | - |
| Two simultaneous callbacks for one state | - | **exactly one of two concurrent transactions wins** |
| Stolen state completed by another admin | `ATTEMPT_NOT_YOURS`; attempt not burned | same; legitimate completion then succeeds |
| Role withdrawn during consent | refused before the exchange | - |
| Grant without a refresh token | refused and revoked | - |

### Refresh

| Case | Offline | Live |
|---|---|---|
| Mints an access token without storing it | pass | `last_refreshed_at` recorded |
| Grant revoked at Google (`invalid_grant`) | REVOKED; credential destroyed | REVOKED with timestamp; reference cleared; row destroyed |
| Timeout | DEGRADED; credential kept; recovers on next success | - |
| Administrator removed the mailbox scope | `SCOPE_REMOVED`; REVOKED | - |

### Revoke

| Case | Offline | Live |
|---|---|---|
| Admin revokes | revoked at Google; credential destroyed | same |
| Google unreachable | `REVOKED_LOCALLY`; credential still destroyed | - |
| Non-admin roles cannot revoke | pass | - |
| A revoked mailbox cannot be refreshed | no Google call | - |

### Reconnect

| Case | Offline | Live |
|---|---|---|
| After admin revocation | CONNECTED; new envelope; one credential | CONNECTED; `revoked_at` cleared; one credential |
| After Google withdrew the grant | pass | - |
| While already connected | replaces, never accumulates | - |
| From DEGRADED | pass | - |

### Scope denial

| Case | Offline | Live |
|---|---|---|
| Mailbox scope unticked | `MAILBOX_SCOPE_DENIED`; grant revoked | same; mailbox stays PENDING; nothing stored |
| Identity scope unticked | `IDENTITY_SCOPE_DENIED` | - |
| `gmail.send` or full mail access granted | `WRITE_SCOPE_GRANTED`; revoked | - |
| Unrelated extra scope | `UNEXPECTED_SCOPE_GRANTED` | - |
| Revocation fails during a refusal | still refused | - |

### Wrong mailbox

| Case | Offline | Live |
|---|---|---|
| Consent with a personal account | `WRONG_MAILBOX_AUTHORIZED`; revoked | - |
| Consent with **another real shared mailbox in the same organization** | refused; neither mailbox connected | same |
| Credential row copied onto another mailbox | `CREDENTIAL_UNREADABLE`; **no call reaches Google**; DEGRADED | same |
| Callback naming a mailbox | service signature has no such parameter; API returns 422 before the service runs | - |

### Database constraints

Each was required to reject **for its own named constraint**, with positive controls proving valid rows are still accepted. **16/16 passed.**

| Rejected | Named constraint |
|---|---|
| A raw refresh token | `ck_mailbox_credentials_envelope_is_sealed` |
| A credential carrying `gmail.send`, full mail access, or `gmail.modify` | `ck_mailbox_credentials_no_write_scope_in_v1`. **Superseded 2026-10-05** by `ck_mailbox_credentials_no_send_capable_scope` (migration 0010), which permits `gmail.compose` and refuses these three plus `gmail.insert`, `gmail.labels` and both `gmail.settings` scopes. See the addendum to `CLIENT_C12_TEST_REPORT.md` |
| A second credential for one mailbox | `uq_mailbox_credentials_mailbox` |
| A mixed-case account email | `ck_mailbox_credentials_account_email_is_lowercase` |
| An OAuth state stored in recoverable form | `ck_mailbox_connection_attempts_state_hash_is_sha256` |
| A plaintext PKCE verifier | `ck_mailbox_connection_attempts_verifier_is_sealed` |
| A duplicate state hash | `uq_mailbox_connection_attempts_state` |
| A consumed attempt without an outcome | `ck_mailbox_connection_attempts_consumed_attempt_has_outcome` |
| CONNECTED without a credential reference | `ck_mailboxes_connected_requires_credential_reference` |
| REVOKED without a timestamp | `ck_mailboxes_revoked_status_matches_timestamp` |
| Updating or deleting mailbox audit rows | `audit_events is append-only` |

### Web routes

77 web tests, including: redirects only to exactly `https://accounts.google.com/o/oauth2/...` - lookalike hosts, embedded credentials, alternate ports, `javascript:`, protocol-relative, and oversized URLs are all refused; hostile callback input never appears in the redirect; cross-origin, missing, and `null` origins are refused on state-changing routes, checked before authorization; the callback is not a public path and forwards only state and code.

## Defects the verification caught

1. **Expiry masked a replay.** The service checked expiry before consumption, so a state that had already connected a mailbox, presented again after ten minutes, was reported as a stale link - with no audit event. A reused credential is the fact an operator needs to see. Caught by the lifecycle suite; the order is now ownership, replay, expiry, then an atomic consume; two regression tests added.
2. **A live check passed for the wrong reason.** The first live run reported "a second credential for one mailbox is refused" as passing, but the error was *"cannot insert multiple commands into a prepared statement"*: the driver rejected my two-statement query and the unique index was never reached. The helper had accepted any database error as proof. Caught on reviewing the output, not by the tooling. Every constraint check was rerun requiring its **named** constraint in the error, with positive controls: 16/16.
3. **A test depended on router internals.** Listing `app.routes` failed because included routers expose no `.path` in this FastAPI version. The test now reads the published OpenAPI contract, which is exactly the surface a caller can reach.
4. **A verification script silently skipped its own evidence.** The first migration apply ran inside a script that then aborted on assigning to `status`, a read-only variable in zsh - so the apply's result was never checked and nothing after it ran. Rerun on a freshly created database with the result captured.
5. **C02 granted the Gmail secret to the wrong service.** The Terraform definition gave the Gmail OAuth client secret to the worker only, but the internal API performs the code exchange. Corrected to `api` and `worker`, and re-validated. The web tier still holds neither the secret nor the keyring.

## What C06 deliberately did not do

- **Connected no real mailbox.** That requires the client's Internal OAuth client, a named mailbox, and explicit approval.
- **Read no mail.** Gmail watches, Pub/Sub notifications, history sync, and ingestion into the C04 tables are C07.
- **Built no mailbox administration screens or permission-grant endpoints.** The access decision and the `mailbox_permissions` table are in place and enforced; the surfaces that manage them are C08.
- **Did not wire refresh into a worker.** `refresh` is implemented and verified; scheduling and caching access tokens across 50 mailboxes is C07.
- **Did not automate key rotation.** The keyring supports it and the runbook describes it; an automated reseal job belongs with C13.
- **Created no path that can send, draft, label, or modify mail.**

## Residual risks

| Risk | Treatment |
|---|---|
| The full migration chain including pgvector has not been applied since `0006` | `0006` touches no pgvector object, and its upgrade and downgrade were verified with real Alembic. Rerun `alembic upgrade head` on the pgvector container when Docker is available, before C07 relies on it |
| Real Google may differ from the simulation - response shapes, scope aliases, granular consent | Scope normalisation covers the documented aliases. Verify with the client's Internal OAuth app during the C14 pilot, against a test mailbox, before any production mailbox |
| An unexpected exception inside `complete` rolls back the attempt's consumption | The authorization code is single-use at Google, so a replayed code fails the exchange. Expected failures return outcomes and commit |
| Every refresh mints a new access token | Fine for connection; at C07's scale the worker should cache access tokens in memory until expiry |
| The internal API trusts the BFF's actor headers and assumes an ACTIVE membership | By design: the BFF resolves only ACTIVE memberships, and the API is reachable only inside the VPC (C-D021). Documented in code |
| Origin checks refuse requests without an `Origin` header | Correct for browsers, which send one on these requests. No non-browser client exists; revisit if one is added |
| Key rotation is manual | Additive and recoverable by design; automate at C13 |

## Preserved working-tree state

`ResolveFlow-AI-Presentation.pptx` remained deleted and untouched. The untracked planning documents, local `.env`, and `app/data` were not modified. The MVP core and its tests are unmodified. The scratchpad PostgreSQL was stopped and its data directory removed; the Homebrew PostgreSQL service state was not changed.

## Gate conclusion

C06 meets its gate: connect, refresh, revoke, reconnect, scope denial, and wrong-mailbox handling are each proven offline and end to end against real PostgreSQL, and the database refuses every unsafe credential state for its named reason. C07 may begin as code; connecting any real mailbox still requires the client's OAuth client and explicit approval.
