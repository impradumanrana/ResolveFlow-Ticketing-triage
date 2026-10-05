# Mailbox Connection Modes

**Audience:** the client's Google Workspace administrator and the delivery team.

**Status (updated 2026-10-05):** the connection mechanism is built and tested against a simulated Google. No real mailbox has been connected. Connecting one requires the client's OAuth client, a named mailbox, and explicit approval.

ResolveFlow reads mail, and - where the client has authorized it - creates **drafts** in the connected mailbox for a person to review and send themselves. It never sends, labels, archives, or deletes. Everything below follows from that.

## The four things people call "a mailbox"

Workspace has four distinct objects that all look like an email address to a customer. They have different permission models, and treating them as the same thing is how a product ends up reading the wrong inbox or asking for far more access than it needs.

| Mode | What it is | Can ResolveFlow connect it? | How |
|---|---|---|---|
| **Shared / role mailbox** | A real Workspace user account used as a team inbox, e.g. `support@client.com` | **Yes - the V1 path** | OAuth, signed in *as that mailbox* |
| **Individual user mailbox** | A person's own inbox | Technically yes; **not recommended** | OAuth, as that person |
| **Google Group** | A distribution list or collaborative inbox. It has no inbox of its own - it delivers into members' mailboxes | **No** - refused with `MAILBOX_KIND_NOT_CONNECTABLE` | Connect a real mailbox that receives the group's mail |
| **Alias / send-as address** | A second address that delivers into, or sends from, an existing mailbox | **No** - refused with `MAILBOX_KIND_NOT_CONNECTABLE` | Connect the underlying mailbox; the alias's mail is already in it |

A fifth option exists and is deliberately excluded from V1:

| Mode | What it is | V1 position |
|---|---|---|
| **Domain-wide delegation** | A service account the Workspace admin authorizes to impersonate *any* user in the domain | **Not used.** It grants technical access to every employee's mailbox. The product needs one or two support inboxes. Revisit only for a contracted enterprise need, with an explicit allowlist and security review (C-D007). |

### Why not connect a Google Group directly

A group is a routing rule, not a store of mail. There is no OAuth-authorizable account behind it, and Gmail's API reads mailboxes, not groups. The reliable pattern is a shared mailbox that is a member of the group - then every message the group receives lands in an inbox ResolveFlow can read.

### Why individual mailboxes are not recommended

Connecting a person's own mailbox brings their private mail into scope, ties the connection to one employee's account lifecycle (it breaks when they leave), and makes the audit trail attribute a team inbox to an individual. Use a shared mailbox.

## What is requested, and what is refused

There are two scope profiles. The deployment runs one of them, set by `RESOLVEFLOW_MAILBOX_SCOPES`, and the default is read-only.

| Profile | Scopes requested | What it enables |
|---|---|---|
| `read_only` (default) | `gmail.readonly`, `openid`, `userinfo.email` | Reading mail and triage. An approved answer is recorded but no draft is created; the reviewer is told `DRAFT_SCOPE_NOT_GRANTED`. |
| `read_and_draft` | the same, plus `gmail.compose` | An approved answer becomes a **draft** in the connected mailbox, which a person then reads and sends from Gmail themselves. |

| Scope | Why |
|---|---|
| `gmail.readonly` | Read messages and register a Gmail watch for new mail |
| `openid`, `userinfo.email` | Prove which account actually authorized the connection |
| `gmail.compose` | Create a draft in the mailbox. Requested only under `read_and_draft`, and **optional** - see below |

### About `gmail.compose`

Google's `gmail.compose` scope also permits **sending**. There is no narrower Gmail scope that creates a draft without it. ResolveFlow does not send, and that is enforced in four independent places rather than by the scope:

1. No send endpoint (`drafts/send`, `messages/send`) appears anywhere in the codebase. A test asserts this for every file on every run.
2. The one Gmail write call in the product is `POST /gmail/v1/users/me/drafts`.
3. `organizations.sending_enabled` is constrained to `false` in the database.
4. `provider_drafts` has no column that could represent a send.

A draft sits in the mailbox exactly like one a colleague typed. Nothing reaches a customer until a person opens it and presses send.

### Granular consent

The consent response is checked in **both** directions:

- **Missing** `gmail.readonly` → refused, `MAILBOX_SCOPE_DENIED`. Google's granular consent lets a person untick boxes; "the flow finished" does not mean "access was granted".
- **Missing** `gmail.compose` → **accepted.** The mailbox connects and drafting is simply off. Unticking that one box on the consent screen is a supported choice, not a failure, and it can be granted later by reconnecting.
- **Extra** scopes → refused, `WRITE_SCOPE_GRANTED` or `UNEXPECTED_SCOPE_GRANTED`. `gmail.send`, `gmail.modify`, `gmail.insert`, `gmail.labels`, the `gmail.settings` scopes and full mail access (`https://mail.google.com/`) are refused under **both** profiles, however willingly they were granted. `gmail.compose` is refused under `read_only`.

In the refusing cases the token Google issued is **revoked at Google**, not merely discarded. Discarding would leave a live grant nobody here knows exists. The database independently rejects any stored credential carrying a send-capable scope (`ck_mailbox_credentials_no_send_capable_scope`).

The same check runs on every token refresh. If `gmail.compose` is taken away later, the mailbox stays connected and drafting turns off. If `gmail.readonly` is taken away, access is withdrawn and the credential destroyed.

## Proving the right mailbox was authorized

The authorization URL pre-selects the intended address with `login_hint`, but a person can still choose any Google account on the consent screen. After the exchange, ResolveFlow asks Gmail which address the new token actually reads. If it is not the mailbox being connected, the connection is refused with `WRONG_MAILBOX_AUTHORIZED` and the token is revoked.

The callback itself carries no mailbox identifier. The mailbox comes from the stored, single-use connection attempt, so a crafted callback cannot redirect a token onto a different inbox.

## Credential custody

- The refresh token is encrypted with AES-256-GCM before it reaches the database, under a key held in Secret Manager.
- The ciphertext is bound to its organization and mailbox. A credential row copied onto another mailbox fails to decrypt, and ResolveFlow will not call Google with it.
- Access tokens are never stored. They are minted when needed, held in memory for at most 45 minutes, and discarded.
- Disconnecting destroys the local ciphertext immediately, even if Google's revocation endpoint is unreachable. That case is recorded as `REVOKED_LOCALLY` so an operator can confirm the grant is gone at Google.

## Mailbox status

| Status | Meaning | What happens |
|---|---|---|
| `PENDING` | Defined, never connected | Nothing is read |
| `CONNECTED` | Valid credential, last refresh succeeded | Mail is read |
| `DEGRADED` | Temporary provider failure, or a credential that cannot be opened | Credential kept; retried; alerting applies |
| `REVOKED` | Grant withdrawn - by an administrator here, or at Google (password change, admin removal, user revocation) | Credential destroyed; reconnect required |

Reconnecting is the same flow as connecting. It replaces the credential rather than adding a second one.

## Setting up the OAuth client (client administrator)

1. In the client's Google Cloud project, create an OAuth client of type **Web application**.
2. Set the OAuth consent screen user type to **Internal**. An Internal app used only inside the client's Workspace organization does not require Google's restricted-scope verification for `gmail.readonly`. An External app does, and that review takes weeks.
3. Add the redirect URI: `https://<client web host>/api/mailboxes/oauth/callback`.
4. Store the client secret in Secret Manager as `rf-<env>-gmail-oauth-client-secret` (see `infra/docs/RUNBOOK.md`). Never send it by email or chat.
5. Sign in to Google **as the shared mailbox** when an administrator starts the connection in ResolveFlow.

## Turning drafting on (client decision)

Drafting is not a toggle in the product. It changes what the client's own OAuth consent screen asks for, so it needs the client's authorization and a Workspace administrator's action. In order:

1. **Authorize it.** Confirm in writing that ResolveFlow may request `gmail.compose` for the named mailbox, having read "About `gmail.compose`" above.
2. **Add the scope to the OAuth client.** In the client's Google Cloud project, add `https://www.googleapis.com/auth/gmail.compose` to the OAuth consent screen's scope list. An **Internal** consent screen needs no Google verification for it; an External one does.
3. **Deploy with the profile set.** `RESOLVEFLOW_MAILBOX_SCOPES=read_and_draft`, and apply migration `20260918_0010`, which is what allows a compose-scoped credential to be stored at all. Neither alone is enough.
4. **Reconnect the mailbox.** An existing connection keeps the scopes it was granted; Google does not add one retroactively. An Owner or Admin disconnects and reconnects the mailbox, signed in as the shared mailbox, and leaves the compose box **ticked** on the consent screen.
5. **Confirm.** The mailbox shows `CONNECTED`, and approving an answer reports a created draft rather than `DRAFT_SCOPE_NOT_GRANTED`. Check the draft in Gmail before approving a second one.

Turning it back off is the reverse: set the profile to `read_only`, reconnect the mailbox so the grant no longer carries compose, then downgrade the migration. The downgrade **refuses to run** while a compose-scoped credential is still stored, so disconnect first - it will not quietly delete a credential a person consented to.

## Who can do what

| Action | Who |
|---|---|
| Connect, reconnect, or disconnect a mailbox | Owner, Admin |
| View a mailbox's conversations | Owner, Admin, Auditor always; Supervisor and Agent only with an explicit mailbox permission |
| Act on a mailbox's conversations | Owner, Admin always; Supervisor and Agent only with an explicit action permission; Auditor never |

Organization membership never implies mailbox access.
