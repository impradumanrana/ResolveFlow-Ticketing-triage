# ResolveFlow AI - Client Production Decision Log

This root decision log controls the dedicated client production track. `docs/DECISIONS.md` remains the historical hackathon-MVP log.

## C-D001 - One reusable core, explicit deployment profiles

Use one coherent codebase with explicit `client-dedicated` and later `saas` profiles. Do not create unrelated rewrites. Client-specific branding, policy, and infrastructure configuration must stay outside generic core modules.

## C-D002 - Client V1 runs in client-owned Google Cloud

Deploy Next.js, Python services, workers, database, events, files, secrets, logs, and backups in client-owned GCP staging and production. Do not split V1 across Vercel and GCP. Vercel may be reconsidered for a future frontend tier only after a measured benefit and an explicit architecture decision.

## C-D003 - Next.js UI; preserve proven Python AI logic

Build the customer-facing app with Next.js App Router, React, and TypeScript. Retain the verified Python LangGraph, guardrail, RAG, MCP, provider, and evaluation behavior behind authenticated internal APIs. Streamlit is an internal regression/evaluation reference, not the production UI.

## C-D004 - One organization at launch, tenant-safe schema from day one

The client deployment initializes exactly one organization. Every tenant-owned record still carries a server-derived `organization_id`, and authorization is enforced at service/database boundaries. This supports departments and prevents a later destructive migration.

## C-D005 - Workspace identity is invitation plus domain restricted

Use Google Workspace OAuth with Auth.js database sessions, explicit memberships, approved-domain checks, CSRF protection, revocation, and server-side RBAC. A matching email domain alone never grants access.

## C-D006 - Production retrieval uses Cloud SQL PostgreSQL and pgvector

Migrate article/chunk metadata and embeddings from local SQLite/Qdrant concepts to client Cloud SQL PostgreSQL, pgvector, and full-text search. Preserve hybrid retrieval, reranking, citations, grounding validation, and evidence traces. MCP remains an authorized tool boundary, not the database.

## C-D007 - Gmail starts read-only and mailbox-explicit

Begin with 1-2 explicitly authorized shared mailboxes through least-privilege OAuth. Implement watch/history/reconciliation before scale. Aliases, Google Groups, independent mailboxes, and domain-wide delegation are distinct connection modes. Domain-wide delegation is not V1 default.

## C-D008 - No commercial billing features in the client deployment

Do not build or expose pricing, Stripe, subscriptions, entitlements, trials, checkout, invoices, dunning, or upgrade UI for the client track. Client cloud/provider costs are managed in accounts owned by the client.

## C-D009 - Client BYOK secrets remain in client Secret Manager

Model/OAuth credentials must not be stored in source, client bundles, browser storage, ordinary database columns, logs, traces, exports, or analytics. Store tenant-scoped references and masked metadata; execute provider calls server-side.

## C-D010 - Human control and fail-closed routing

Observe Mode is first. The system may later create a Gmail/provider draft only after an authorized human approval. It must never auto-send. Provider, MCP, evidence, schema, permission, or safety failures route to human review with visible reason codes.

## C-D011 - Production mutation requires explicit authorization

C00 and C01 are local planning/foundation work. Infrastructure planning in C02 does not authorize applying Terraform or creating cloud resources. OAuth consent changes, mailbox connection, staging deployment, production deployment, sending-scope changes, and production data migration each require explicit approval and resolved client inputs.

## C-D012 - Client data cannot seed the founder SaaS

The future founder SaaS must use a separate repository/environment and synthetic data unless the contract explicitly permits generic code reuse. Never transfer client messages, knowledge, users, prompts containing confidential content, credentials, logs, brand assets, or environment configuration.

## C-D013 - Unknown client parameters are recorded gates

At C00, unavailable client values are documented in `CLIENT_SCOPE.md` rather than guessed. They do not block C01 repository foundation, but they block the phase that consumes them. Every resolved value must be added here or to a linked approved configuration decision.

## C-D014 - C01 uses a workspace, not a destructive rewrite

The repository root owns reproducible commands and dependencies; `apps/web` contains the Next.js application, `app/api` contains the private FastAPI boundary, and the proven Python core remains in `app`. Streamlit remains runnable as an internal reference while production journeys move to Next.js.

## C-D015 - The Python boundary is versioned and fail-closed

All production-facing Python capabilities live below `/v1`. Pydantic rejects unknown top-level fields, OpenAPI is generated and checked in, both stacks test the same contract, and C01 intentionally exposes no send endpoint. Contract drift must fail CI.

## C-D016 - Runtime versions are explicit

The web foundation is pinned to Node 20.20.2, Next.js 16.3.4, React 19.3.0, and a committed npm lockfile. Python dependencies and development quality tools are pinned through requirements files; the supported Python contract remains 3.11 or later.

## C-D017 - Client orchestration is additive

`compose.client.yml` and the two production-shape Dockerfiles are additive. They do not replace the Streamlit MVP container or mutate ignored local knowledge data. The FastAPI service remains private in the target GCP design.

## C-D018 - C01 credentials are development scaffolding only

The internal bearer token and organization header validate service boundaries during local development. They are not user authentication, membership, or tenant authorization. C03 must derive organization and role context from an authenticated server-side session; real secrets belong in client Secret Manager.

## C-D019 - Terraform is the infrastructure-as-code tool, with per-environment state

Define all client cloud resources in Terraform under `infra/terraform`. Each environment is a separate root module with its own remote state prefix in a client-owned, versioned, access-audited GCS bucket. The state bucket is created once by `infra/terraform/bootstrap`, which is the only configuration that cannot use remote state. Provider versions and checksums are pinned in committed `.terraform.lock.hcl` files for the CI and developer platforms.

## C-D020 - Staging and production are fully separate, sharing nothing

Each environment has its own client-owned project, VPC, database, buckets, KMS keys, secrets, image repository, service accounts, and CI identities. Subnet ranges do not overlap. Promotion is a deliberate CI action against a specific image digest. No resource, credential, or dataset is shared, so a staging mistake cannot reach production data.

## C-D021 - Only the web tier is publicly reachable

The Next.js web/BFF service is the single public surface. The Python AI API and the ingestion worker are `INGRESS_TRAFFIC_INTERNAL_ONLY` and are reached only through direct VPC egress from the web tier, or by Pub/Sub push and Cloud Scheduler with an OIDC token. The `runtime` module refuses a configuration that exposes a service marked `private_tier`, and refuses more than one public service.

Workspace login, invited membership, and RBAC are enforced by the application (C03), not by Cloud Run IAM. Staging therefore stays restricted to a named client tester group; `allUsers` is set on production only at signed launch approval. Edge protection (Cloud Armor, IAP, a load balancer, and a mapped domain) is deferred to C13 because no client domain exists yet.

## C-D022 - Cloud SQL is private, IAM-authenticated, and has no password

The instance has no public IP and no authorized networks; it is reachable only over private service access and requires encrypted connections. Services authenticate as `CLOUD_IAM_SERVICE_ACCOUNT` database users, so no database password exists to store, rotate, leak into Terraform state, or find in a log. Point-in-time recovery is enabled in both environments, and production is regional with deletion protection.

pgvector and every table remain owned by the Alembic migration chain (C04/C05). Terraform provisions the instance, the database, and access only, so schema history keeps one source of truth.

## C-D023 - Terraform never holds a secret value

The `secrets` module creates empty Secret Manager containers and per-secret accessor grants. It creates no secret version and uses no generated password, so no credential enters the repository, a plan output, or state. Values are injected out of band by an authorized client operator. An automated check fails the build if a secret version or generated password is ever added to the configuration.

Access is granted per secret to one runtime identity. No principal holds a project-wide `secretmanager.secretAccessor` role.

## C-D024 - CI is keyless, and deploy privilege is separated from Terraform privilege

GitHub Actions federates through Workload Identity Federation; no service account key is created or stored. The provider carries an attribute condition pinning it to one repository. Two identities exist per environment: `deploy` pushes images and rolls Cloud Run revisions with no secret access, and `infra` runs Terraform and is deliberately broad, constrained instead by a protected GitHub environment requiring manual approval, by required review, and by an access-audited state bucket.

## C-D025 - Terraform does not own deployed images, and migrations run as a job

Cloud Run services are created with a placeholder image and ignore image drift, so `terraform apply` can never silently roll a running service back to an older revision. Migrations run as a dedicated Cloud Run job from CI, before new revisions serve traffic, never on container start-up, because a service that migrates on boot races itself across revisions.

## C-D026 - C02 verification is offline and self-testing

C02 defines infrastructure and applies none of it, so its gate is verified without cloud credentials: `terraform fmt`, `terraform validate` against the real provider schema, and `scripts/validate_infra.py`, which parses the HCL to check module wiring and the security posture decisions above. Every posture check has a paired negative test that breaks a copy of the definition and requires the check to fail, so the suite cannot pass vacuously. The offline checks do not depend on the Terraform binary, because neither CI nor a developer machine may be assumed to have it.

## C-D027 - Auth.js with Google Workspace OAuth and database sessions

Implement C-D005 with Auth.js (`next-auth` 5) and the PostgreSQL adapter. Sessions are database rows, not JWTs: when access is withdrawn it must stop working on the next request, and a self-contained token cannot be withdrawn. PKCE, state, nonce, and CSRF are handled by the library; this codebase supplies policy, not protocol.

Sign-in requests `openid email profile` only. Mailbox scopes are never bundled into login; C06 adds an explicit, separately auditable mailbox-connection flow.

The session cookie name is pinned explicitly rather than inferred, because the request guard reads that cookie directly to check server-side revocation. A disagreement between the library's default and the guard would silently sign everyone out.

## C-D028 - Authorization is a pure function over a server-derived context

All access decisions run through one pure function, `authorize(context, permission, resource)`, in a module with no framework, database, or network imports. It is given a context the server built from database records and returns a decision plus a stable reason code. Nothing it reads can be influenced by the caller.

This is what makes exhaustive adversarial testing cheap: the full role-by-permission matrix, every membership status, cross-organization object access, and department scoping are all verified offline, with no server and no database, on every commit.

Roles are deliberately not ordered. An Auditor outranks an Agent on `audit.view` and is below them on every operational action, so no "greater than" comparison exists anywhere in the codebase.

## C-D029 - Tenant isolation is checked before role, and read-only roles are enforced twice

`authorize` evaluates the resource's organization before the role matrix. Both orderings deny, so this is an audit decision rather than a security one: a reference to another organization's object is a security event worth naming precisely, and it must be reported identically whichever role attempted it. Checking the role first would report the same attempt as `ROLE_LACKS_PERMISSION` for some roles and as a cross-tenant reference for others.

Separately, `READ_ONLY_ROLES` are refused every `WRITE_PERMISSIONS` entry independently of the matrix. If a later edit mistakenly grants an Auditor a write permission, the request is still refused. Two independent expressions of the same intent, so one mistake is not enough to cause a breach.

## C-D030 - Identity is re-derived from the database on every protected request

`resolveIdentity` validates the session row (existence, revocation, expiry), then the user, then the membership, and only then builds a frozen `AuthContext`. Role, organization, department, and membership status are never read from a cookie, header, query parameter, or request body, and the session payload is not trusted as an authority.

The cost is a database read per request; the benefit is that revoking a membership takes effect immediately rather than whenever a token happens to expire. An unrecognised role or status from the repository is refused rather than defaulted, because an adapter is an interface and could return anything.

## C-D031 - Middleware is a filter, not the authorization boundary

The Next.js middleware rejects requests with no session cookie and sets security headers. It deliberately makes no role or organization decision: it runs at the edge without database access, and the presence of a cookie proves nothing. Every protected page and route handler still calls `requirePageAccess` or `requireAccess`. Tests assert that the middleware never imports the authorization or identity modules.

## C-D032 - The internal boundary carries server-derived actor context

The private Python API now accepts organization, membership, and role headers, supplied by the Next.js BFF from its own `AuthContext`. The BFF forwards nothing from the incoming browser request, so a caller cannot choose the organization it is treated as.

The Python service has no database and does not re-check membership; its guarantee is narrower and stated as such in code. It authenticates the calling service and refuses malformed or unknown context - an unrecognised role is a 400, not a default. Being reachable only from inside the VPC (C-D021) is what makes that sufficient. Actor headers are optional so scheduled work need not impersonate a person, but membership and role must arrive together: half an actor is not an actor.

This retires the C01 limitation recorded in C-D018.

## C-D033 - The bootstrap Owner is seeded, never invited

Exactly one organization is created, by one script, run deliberately by an authorized operator. It refuses rather than overwrites when an organization already exists, because "bootstrap ran twice" and "someone is re-pointing this deployment at a different company" are indistinguishable from inside the script.

The first Owner is created ACTIVE because nobody yet holds the authority to invite them. The database forbids inviting an Owner at all (`ck_invitations_owner_is_not_invitable`) and permits only one active Owner per organization, so an invitation flow can never mint one.

Planning is separated from execution: `build_plan` is pure and fully tested offline, and refuses an Owner whose domain is not on the approved list, which would otherwise produce a deployment nobody can sign in to.

## C-D034 - Audit events are append-only in the database, and content is never audited

A trigger rejects UPDATE and DELETE on `audit_events`. An application bug, a compromised application identity, or an operator with table access cannot quietly rewrite history; removing the guarantee requires dropping the trigger, which is a DDL change that C02's pgaudit configuration records.

Audit metadata is sanitised before writing: credential- and content-shaped keys are redacted, values are truncated, and source addresses are stored as a salted hash rather than raw. An audit trail that contains the data it protects is a second copy of the risk. Audit write failures are logged and swallowed so that a failing sink cannot take down sign-in; C12 and C13 should revisit whether their actions need hard-fail auditing.

## C-D035 - The offline test build keeps the authorization core framework-free

`tsconfig.test.json` compiles only the framework-free modules to `.test-build`, which `node --test` then exercises. Node 20 - the pinned runtime - cannot strip types at runtime, and adding a TypeScript test runner would add a dependency to the security-critical path.

This imposes a useful constraint: anything reachable from the authorization core must not import Next, React, `pg`, or `next-auth`. Server-only modules carry the `server-only` guard so the build fails if one is ever pulled into a client component, and a test asserts that guard is present on every such file.

## C-D036 - C04 is two migrations, split by concern

The durable model lands as `20260915_0003` (queues, mailboxes, threads, messages, attachments, tickets, assignments, seen state, SLA) and `20260915_0004` (knowledge, embeddings, AI config, triage runs, actions, jobs, usage, evaluations, retention). Splitting them means a failure in one is not a failure in both, and each is small enough to review.

Both extend the C03 identity schema rather than replacing it. `test_migration_foundation.py` owns the chain mechanics and `test_schema_invariants.py` owns schema content, so a new migration updates one expectation rather than two.

## C-D037 - Provider identifiers are unique per mailbox, never globally

`messages` is unique on `(mailbox_id, provider_message_id)` and `threads` on `(mailbox_id, provider_thread_id)`. Two authorized mailboxes legitimately receive the same Gmail message or thread; a globally unique constraint would make the second mailbox silently drop the conversation, which looks like an ingestion gap rather than a schema decision.

These indexes are the idempotency guarantee C07 depends on: Pub/Sub delivers at least once, so redelivery must be a rejected insert rather than a duplicate ticket. `jobs` and `actions` carry explicit idempotency keys for the same reason, and `triage_runs` is unique on its correlation id.

## C-D038 - The fail-closed grounding rule is enforced by the database

`triage_runs` refuses to store an `AUTO_RESOLVE` draft that is not grounding-validated and does not carry at least one citation. The application already enforces this (the MVP's proven behaviour), but an application bug, a migration, or a direct statement could otherwise persist an ungrounded draft that a reviewer would then see as approved-looking output.

Two independent expressions of the same rule, so one mistake is not enough. `organizations.sending_enabled` is likewise pinned false by a check constraint: no C04 change can weaken the C12 prohibition.

## C-D039 - Embeddings record the model and dimension that produced them

`knowledge_embeddings` stores `embedding_model` and `dimensions` alongside the vector, constrained to the configured 1536. Vectors of the same length from different models are still comparable arithmetic - they just produce meaningless neighbours - so a provider silently changing its embedding model would corrupt retrieval with no visible failure. Changing the model becomes a migration and a reindex, not an accident.

The vector index is HNSW with `vector_cosine_ops` rather than IVFFlat, because IVFFlat needs a training pass over existing data and a client's corpus starts empty. Full-text search uses a generated `tsvector` column with a GIN index, preserving the hybrid dense-plus-keyword retrieval the MVP proved.

## C-D040 - A BYOK key may never occupy a column

`ai_configs` stores a Secret Manager resource name and at most the last four characters for display. A check constraint rejects values that begin `sk-` or `AIza` and any value over 512 characters, so an accidental write of a real key fails loudly rather than leaking quietly.

The constraint uses `left(value, 3) <> 'sk-'` rather than `NOT LIKE 'sk-%'`: a `%` in raw SQL is escaped to `%%` in Alembic's offline output, which would be wrong if an operator applied the rendered SQL directly.

## C-D041 - Retention is policy plus legal hold, never a bare DELETE

Deletion is expressed as `retention_policies` rows, an `active legal_holds` check, and an append-only `retention_sweeps` log. `build_sweep_plan` is pure and decides what *would* be deleted; `execute_sweep` defaults to a dry run and must be asked to delete.

Rules that follow from this: absence of a policy is never permission to delete; a retention value outside 1-3650 days is refused rather than clamped; duplicate policies for one data class are refused as ambiguous; a naive timestamp is refused because a cutoff computed in an unknown zone deletes the wrong rows.

`AUDIT_EVENT` and `TICKET` are deliberately unsweepable by age. Audit history is append-only at the database level, and deleting a ticket orphans its conversation and decision history - ticket deletion is a C13 privacy workflow with its own approval, not a scheduled job.

## C-D042 - Per-user seen state is ResolveFlow's, not the provider's

`ticket_seen_state` records, per membership, when a person last saw a ticket and through which message. Gmail's `UNREAD` label is shared by everyone with mailbox access and therefore cannot answer "has this agent seen this ticket". A shared label would make "new to me" wrong for every agent but the first.

## C-D043 - Ticket numbering is per organization, assigned by a trigger

A trigger computes the next `reference` within the organization on insert. A shared sequence would leak one organization's volume to another once this core runs multi-tenant, and gaps in a customer-visible number invite support questions about missing tickets.

## C-D044 - SLA is evaluated against a recorded time zone

`sla_policies` carries `time_zone`, business-day bounds, and the set of business days, and is versioned with an effective window. Business hours computed in the server's zone are wrong by the UTC offset for every client outside it, and "wrong by a few hours" in an SLA is a contractual problem rather than a display bug.

## C-D045 - Every timestamp is timezone-aware, asserted by a test

Every `TIMESTAMP` column in the chain is `WITH TIME ZONE`, and a test walks the rendered SQL to prove it. A single naive column silently makes SLA, retention, and audit ordering wrong by the server's offset, and that is exactly the kind of defect that survives review.

## C-D046 - Tokenisation and fuzzy coverage are imported from the MVP, not reimplemented

`app/knowledge/text.py` re-exports `_tokens` and `_fuzzy_coverage` from `app.knowledge_store` rather than copying them. The rerank depends on exact token sets, so a second tokeniser that drifted by one stop word or one concept group would change scores in a way that looks like a retrieval regression and is almost impossible to attribute afterwards.

Importing private names across modules is normally poor practice. It is the right trade here because the alternative is two implementations of the one thing that must not diverge, and the coupling is recorded rather than accidental.

## C-D047 - The rerank formula is preserved; only its sources change

Production scoring is the MVP's formula, component for component: `0.46*semantic + 0.28*lexical + 0.08*fuzzy + 0.08*keyword + 0.06*category + 0.04*rrf`, with the same corroboration bonus and the same fail-closed rule that zeroes a result carrying no semantic, lexical, or keyword evidence.

What changed is where components come from. Dense candidates come from pgvector and lexical candidates from PostgreSQL full text, both scoped in SQL; scoring happens per passage rather than per article so a citation can name exact offsets.

Numeric parity is therefore not expected and is not claimed - the two paths score different units of text. What is claimed is that retrieval quality does not regress, and that is measured, not asserted. A weight change must fail `tests/test_retrieval_scoring.py`, be reasoned about, and be re-measured against the labelled cases.

## C-D048 - Full-text terms are ORed, not ANDed

`plainto_tsquery` ANDs every lexeme, so "where can I find a receipt for my purchase" only matches a passage containing all of *find*, *receipt*, and *purchase*. In practice no passage matched, and the lexical arm of the hybrid contributed nothing at all while appearing to work.

Candidate generation therefore builds an explicit OR tsquery from the shared tokeniser's raw terms. Terms are validated against `[a-z0-9_]` and dropped rather than escaped if they fail: a term that cannot be validated has no business in a tsquery. Synthetic `concept_*` tokens are excluded because they exist to bridge paraphrases in the reranker and are not in the text index.

## C-D049 - Curated per-article search terms are a first-class capability

`knowledge_articles.search_terms` stores vocabulary a knowledge manager attaches to an article, bounded at 24 entries and indexed with GIN.

This restores a signal the proven corpus has and the first C05 port dropped, which cost one labelled case: a query for "receipt" retrieved the invoice article rather than the order-confirmation article whose curated terms name a receipt explicitly. Customers say "receipt" for what an article calls an "order confirmation", and no amount of embedding similarity recovers vocabulary the document never uses.

Terms are stored apart from the body, never appended to it, because appending would place them inside quoted citation passages.

## C-D050 - Citation offsets are an enforced invariant, not a convention

Every chunk satisfies `body[start_offset:end_offset] == content`, verified by `verify_offsets` on every chunking run rather than only in tests. Normalisation happens exactly once, during extraction, so the stored body is what offsets are measured against; a later stage that re-normalised would invalidate every citation.

A citation that points at the wrong passage is worse than a failed ingestion, because it looks like evidence.

## C-D051 - Scoping happens in SQL, and an unpublished article is unreachable

Organization, publication status, soft deletion, and archival are predicates in both candidate queries. Filtering after the fact would still have pulled another tenant's passages into the process, and a later refactor that forgot the filter would silently start returning them.

Archived content stays in the database for audit and restore but cannot reach a draft. Re-indexing replaces an article's chunks rather than merging, because a stale chunk would keep serving text that no longer matches the body at those offsets.

## C-D052 - A partly embedded article is never published

If embedding fails midway, the article is marked FAILED with its reason and stays unpublished. Half an index is worse than none: retrieval would return some passages and silently miss others, which reads as "the knowledge base does not cover this" rather than as a failure.

## C-D053 - Clearing all knowledge requires an exact typed phrase

`clear_all` refuses anything but `DELETE ALL KNOWLEDGE`, case and spacing exact. This destroys the corpus the product answers from, and a click is not a decision.

## C-D054 - The offline embedder matches the production vector width

The MVP's deterministic test embedder produces 384 dimensions; the stored column is `vector(1536)` with a check constraint. `app/knowledge/embeddings.py` reuses the MVP's exact hashing scheme at the production width so tests run with no network and no paid call while still writing vectors the schema accepts. Like the deterministic model provider (D-012), it is reachable only when tests set `RESOLVEFLOW_TEST_MODE`.

## C-D055 - Gmail access is read-only, and granted scopes are checked in both directions

A mailbox connection requests exactly `gmail.readonly`, `openid`, and `userinfo.email`. The consent response is then checked against that set both ways. A missing `gmail.readonly` is refused (`MAILBOX_SCOPE_DENIED`), because Google's granular consent lets a person untick boxes and "the flow finished" does not mean "access was granted". Any *extra* scope is also refused (`WRITE_SCOPE_GRANTED` or `UNEXPECTED_SCOPE_GRANTED`): a token able to send, compose, modify, or fully access mail is never stored in a read-only deployment, even if granted willingly - most plausibly through previously granted scopes being merged back in, which the authorization URL also prevents with `include_granted_scopes=false`.

The database independently rejects any stored credential carrying `mail.google.com`, `gmail.send`, `gmail.compose`, or `gmail.modify`.

## C-D056 - Refresh tokens are sealed and bound to their one mailbox

A refresh token is encrypted with AES-256-GCM before it reaches the database, under a keyring supplied from Secret Manager (C02 `mailbox-token-encryption-key`). The associated data names the organization, the mailbox, and the purpose, so a credential row copied onto another mailbox fails authentication and the service refuses to call Google with it - the mailbox is marked DEGRADED instead.

Every envelope records the key id that sealed it. `mailboxes.credential_secret_name` records that key reference, which gives the C04 "connected requires a credential reference" rule a real meaning: a connected mailbox always states how its credential can be opened. The keyring supports rotation: new writes use the first key, older envelopes stay readable until resealed.

Access tokens are never persisted. They live for an hour and are minted from the refresh token when needed; storing them would only widen what a database compromise yields. The PKCE verifier is sealed the same way, and the OAuth state is stored only as a SHA-256 hash. The database rejects a raw refresh token, an unsealed verifier, and a non-hash state.

## C-D057 - The OAuth callback cannot choose the mailbox

`complete` accepts only the state and the authorization code. The mailbox comes from the stored connection attempt the state identifies, so a crafted callback cannot direct a token onto a different inbox. This holds at three layers: the service signature has no mailbox parameter, the internal API contract forbids unknown fields so a `mailbox_id` in the body is a 422 before the service runs, and the web callback route forwards only `state` and `code`.

## C-D058 - The account Google returns is verified, not assumed

The authorization URL pre-selects the intended mailbox with `login_hint`, but a person can still choose any Google account on the consent screen. After the exchange, the service asks Gmail which address the new token actually reads and refuses the connection (`WRONG_MAILBOX_AUTHORIZED`) unless it equals the mailbox being connected. The subtle case matters most: an approved domain and a real shared mailbox, just not this one.

## C-D059 - A token that fails policy is revoked at Google, not merely discarded

When a connection is refused after tokens were issued - wrong scopes, wrong account, no refresh token, unreadable profile - the service calls Google's revocation endpoint before returning. Merely not saving the token would leave a live grant at Google that nobody here knows exists. A revocation failure is recorded but never turns a refusal into a success.

## C-D060 - Connection attempts are single-use and bound to their initiator

An attempt expires after ten minutes and can be completed once, by the membership that started it. Ownership is checked **before** the attempt is consumed, so a stolen state presented by someone else cannot burn the legitimate person's attempt. Consumption is a conditional UPDATE, so two simultaneous callbacks cannot both win; this was verified with two concurrent database transactions.

Replay is checked **before** expiry. A consumed state presented again is a reused credential and is reported and audited as `ATTEMPT_REPLAYED` even after the attempt has also expired. The first implementation checked expiry first and relabelled a replay as a stale link with no audit event; that was caught by the lifecycle suite and fixed.

## C-D061 - Refresh failures are classified, not retried blindly

`invalid_grant` - the grant was revoked by the user, an administrator, a password change, or expiry - withdraws the mailbox: the credential is destroyed and the status becomes REVOKED. A refresh that returns without `gmail.readonly` is treated the same way (`SCOPE_REMOVED`). A timeout, 5xx, or 429 degrades the mailbox and keeps the credential for the next attempt. A credential that cannot be opened degrades the mailbox without calling Google, because a missing rotation key looks identical and deleting would destroy something recoverable.

## C-D062 - Local revocation always proceeds

Disconnecting a mailbox destroys the local credential and marks it REVOKED even when Google's revocation endpoint is unreachable. That outcome is `REVOKED_LOCALLY` with the remote failure audited, so an operator can confirm the grant is gone at Google. A person who asked to disconnect a mailbox must never be left connected because a provider was slow.

## C-D063 - Groups and aliases are not connectable; domain-wide delegation is excluded

Only `SHARED_MAILBOX` and `USER_MAILBOX` kinds may start a connection. A Google Group is a routing rule with no inbox behind it, and an alias is an address that delivers into an existing mailbox; connecting the underlying mailbox is the correct path in both cases. Domain-wide delegation remains outside V1 (C-D007): it grants technical access to every employee's mailbox when the product needs one or two support inboxes. The modes are documented for the client's administrator in `docs/MAILBOX_CONNECTION_MODES.md`.

## C-D064 - Gmail connection uses its own OAuth client, held only by the Python tier

The Gmail OAuth client is separate from the sign-in client, so the restricted `gmail.readonly` scope never sits on the client every staff member signs in through. The client should be created as an **Internal** app in the client's Workspace organization, which avoids Google's restricted-scope verification for an app used only inside that organization.

C02 originally granted the Gmail client secret to the worker only. The authorization code is exchanged by the internal API during connection, so the grant now includes `api` as well as `worker`. The browser-facing web tier still never holds the secret or the token keyring.

## C-D065 - Mailbox access needs an explicit permission unless the role is organization-wide

Organization membership never implies mailbox access. Owner, Admin, and Auditor see every mailbox by design; Supervisor and Agent need an explicit `mailbox_permissions` row, with separate view and action flags. Auditor may never act. Connecting and revoking are administrative and require Owner or Admin. A permission grant for one mailbox or one person is never accepted as evidence about another.

## C-D066 - The web routes guard against open redirects, reflection, and cross-site requests

The connect route redirects the administrator only after confirming the URL returned by the internal API is exactly `https://accounts.google.com/o/oauth2/...` - no other host, port, credentials, or scheme. If that service were misconfigured or compromised, an unchecked redirect would hand an administrator to a lookalike consent page.

The callback's query string is attacker-influenceable, so nothing from it is echoed: outcomes collapse to five fixed categories. State-changing routes refuse a request whose `Origin` does not match, including a missing one. The callback is deliberately not a public path; the session identifies who is completing the flow.

## C-D067 - Mailbox refusals are returned, not raised

The connection service writes an audit event when it refuses, inside the request's database transaction. Raising an HTTP exception from the endpoint would propagate into the dependency and roll the transaction back, losing the evidence of the refused attempt. Refusals are therefore returned as 403 responses, which commit.

## C-D068 - No Google SDK on the credential path

Google's token, revocation, and Gmail profile endpoints are plain HTTPS. The client is implemented directly over an injectable transport, using the already-pinned `httpx`. This keeps the dependency surface of the most sensitive path unchanged and makes every provider behaviour - denied scope, revoked grant, timeout, wrong account - reproducible in tests without a Google account. Tokens never appear in exception messages, reprs, or audit rows.

## C-D069 - A notification is a hint, never a description of what changed

A Gmail push notification carries no message content and its `historyId` is not
trusted as a starting point. Every sync reads from the mailbox's own stored
cursor, so a duplicated, delayed, or out-of-order notification converges on the
same result. A notification whose history id is far ahead, far behind, or
fabricated changes nothing about what is fetched.

This is what makes at-least-once delivery survivable: the notification's only
real content is "this mailbox changed, go and look".

## C-D070 - The cursor advances only after the window's work succeeded, and only forwards

Messages are ingested first; the cursor moves afterwards. A crash mid-sync
leaves the cursor where it was and the next run redoes the window, which is
safe because every write is idempotent. The alternative - advancing first -
turns any crash into permanently missed mail.

Monotonicity is enforced in SQL, not in Python: the UPDATE carries its own
`history_cursor < :cursor` predicate, so two concurrent syncs cannot interleave
a read and a write and rewind the mailbox.

## C-D071 - Deduplication is structural, not a lookup

Three separate mechanisms, each a database constraint rather than a check the
code must remember to perform:

* The Pub/Sub `messageId` is the job idempotency key, so one notification
  queues exactly one sync however many times it is delivered. The history id is
  deliberately *not* part of the key - including it would let a redelivery
  through whenever Gmail restated the notification.
* Messages and threads use the C04 per-mailbox unique indexes with
  `ON CONFLICT DO NOTHING`. Two mailboxes legitimately receiving the same Gmail
  message each keep their own row, which is why the index is per mailbox.
* Thread counters are recomputed from the messages rather than incremented, so
  a replayed window cannot inflate them.

Message ids are also de-duplicated before fetching, so a message repeated
across history records costs one API call rather than several.

## C-D072 - Every Gmail failure has one correct recovery, and they differ

| Failure | Recovery |
|---|---|
| 404 on history - the cursor predates Gmail's retained window | Reconcile by listing messages |
| 404 on a message - deleted between notification and fetch | Skip it |
| 429, or 403 with a quota reason | Defer with bounded exponential backoff |
| 401, or 403 without a quota reason | Stop: the grant or the delegation is gone |
| 5xx | Transient; retry with backoff |

Treating these alike is how ingestion either spins forever on a permanent
failure or gives up on a temporary one. A revoked grant in particular completes
its job rather than retrying: the connection service has already withdrawn the
mailbox, and redelivery would only reproduce the refusal.

## C-D073 - A stale cursor triggers reconciliation, and reconciliation supplies the new cursor

Gmail retains roughly a week of history. A mailbox that was degraded,
disconnected, or simply quiet for longer cannot be caught up incrementally.
Reconciliation lists recent messages directly, ingests whatever is missing, and
then registers a fresh watch - whose response carries a current history id that
becomes the new cursor. Without that last step the next sync would start from
the same stale position and fail again.

## C-D074 - Watches are renewed well before they expire

Gmail expires a watch after about seven days, and an expired watch is silent -
the mailbox simply stops producing notifications, which looks like a quiet
inbox. Renewal runs against a two-day threshold, so a scheduler outage has
five days of slack before anything is missed.

## C-D075 - Access tokens are cached in memory, never persisted

A token is reused for 45 minutes of its hour, dropped the moment Gmail rejects
one, and never written anywhere. Fifty mailboxes syncing every few minutes
would otherwise mint fifty refresh calls a minute for no benefit, and
persisting them would widen what a database compromise yields (C-D056).

## C-D076 - Jobs are leased, and an abandoned lease returns to the queue

A worker claims a job with a conditional UPDATE, so two workers cannot run the
same one. A worker that dies mid-job leaves it RUNNING; a reclaim pass returns
anything whose lease has expired to the queue. Without that, every crash
permanently strands the notifications that worker was holding.

Attempts are bounded: a job that keeps failing is dead-lettered rather than
retried forever, which is what makes the dead-letter alert from C02 meaningful.

## C-D077 - Email is untrusted input, and normalization is where that is enforced

Timestamps come from Gmail's `internalDate`, never the `Date` header, which the
sender writes and can forge - and which SLA and ordering would otherwise
inherit. Header-derived values have CR and LF stripped, since a header carrying
a newline is how injection travels onward. Attachment filenames are reduced to
a basename with control characters removed. Bodies, subjects, and recipient
lists are bounded.

Attachments are classified but never downloaded here: anything outside the
approved type list, oversized, or empty is recorded as SKIPPED and no bytes are
fetched. The scanner itself is C13's.

## C-D078 - Unseen state is derived, not written by ingestion

`ticket_seen_state.seen_through_message_at` compared against the thread's
latest message answers "is this new to me" per person. Ingestion therefore
writes no seen state at all: a stored unread flag would have to be reset for
every member on every arrival, and would be wrong the moment one of them read
it. Gmail's shared UNREAD label cannot answer the question either (C-D042).

## C-D079 - A customer reply reopens a finished ticket

An inbound message on a RESOLVED or CLOSED ticket sets it back to NEW and bumps
its version. This is the minimum that stops a reply disappearing into a closed
thread; richer routing, reassignment, and SLA restart rules belong to C09.

## C-D080 - Hand-authored CSS and native HTML, not Tailwind and Radix

The architecture table in the plan names Tailwind and Radix/shadcn. C08 deliberately does not adopt them, and this records why rather than letting the deviation pass silently.

The workspace needs a table, filters, selection, and a detail view. Native `table`, `form`, `select`, `input`, `details`, and `a` are accessible by default - correct focus order, correct roles, keyboard behaviour that works before any JavaScript loads - and a component library mostly re-implements that. The browser-facing tier stays free of two more dependency trees on the surface the client's staff use daily. Most importantly, colour lives in an explicit token table that a test parses and checks for contrast; utility classes scattered through markup could not be checked that way.

This is a revisitable decision, not a principle. If the workspace grows dialogs, combo-boxes, or drag-and-drop - where hand-rolled accessibility genuinely is hard - Radix primitives are the right answer and should be adopted for those, with a recorded decision.

## C-D081 - Queue state lives in the URL

View, filters, sort, and page are query parameters. A filtered queue can be linked to a colleague, bookmarked, reopened in a second tab, and reached with the back button, and the server can render exactly that page without a client round trip. Parsing never throws: a hand-edited or stale URL falls back to defaults, because a confusing list is a smaller failure than a blocked agent.

## C-D082 - JavaScript is an accelerator, never a requirement

Filters are a plain GET form and assignment is a server action, so both work without client JavaScript. Keyboard shortcuts (j/k and arrows) and the polling refresh are additive: every row is a real link reachable by Tab, and the shortcut handler stands aside while someone is typing in a field.

## C-D083 - Visibility is decided in SQL, once

Organization, mailbox permission, and department are one predicate applied to every workspace query. A ticket the viewer may not see is never loaded into the process, so a future page cannot leak one by forgetting to filter. The rules mirror the authorization core exactly (C-D028, C-D065), and a test asserts every query in the repository applies the predicate.

A conversation in a mailbox the viewer cannot see answers 404, identical to one that does not exist. Whether a ticket exists in a mailbox you have no access to is itself information.

## C-D084 - "New to me" is derived per person, never stored

Whether a conversation is new is computed by comparing the thread's latest message against that person's own read position. No unread flag is stored: a flag would have to be reset for every member on every arrival and would be wrong the moment one of them read it. Gmail's own UNREAD label is shared by everyone with mailbox access and is not used (C-D042, C-D078). Tests assert no code path reads it and that no unread flag exists.

## C-D085 - One server-side timestamp per page

Relative times and service-level states are measured against a single instant captured with the data, not `Date.now()` per row. Two rows of the same age must not render as different ages because the page took time to build. React's purity rule flags the alternative, and the rule is right.

## C-D086 - Colour is declared as contrast-checked pairs

Every text colour in the workspace is declared in the stylesheet as a `--ws-<role>-fg` / `--ws-<role>-bg` pair. A test parses those declarations, applies the WCAG contrast formula, and fails the build below 4.5:1 for text or 3:1 for meaningful borders. Components reference tokens rather than literal colours, so a colour chosen inline would escape the check.

A browser confirms the computed result on a real page; only this confirms it for every pair, on every commit. Both were run: the stylesheet check caught a border at 2.6:1 that looked perfectly fine.

## C-D087 - Freshness is polling, and it yields to the person

The queue refreshes on an interval rather than through a push channel. A support queue changes every few minutes, and a server-sent stream per agent is a connection to operate, scale, and debug for a freshness nobody can perceive. Refreshing under someone mid-task is worse than slightly stale data, so it pauses while the tab is hidden, while rows are selected, and on request. Genuine push is worth revisiting only if the pilot shows agents waiting on it.

## C-D088 - An empty queue explains itself

An empty list caused by missing access looks exactly like an empty list caused by no work. Where a scoped role has no department assignment or no mailbox permission, the empty state says so and says who can fix it. Found by driving the workspace as a second, less-privileged person rather than by reading the code.

## C-D089 - The framework does not write into the client's repository

Next.js generates `AGENTS.md` and `CLAUDE.md` into the project when the dev server starts. `agentRules: false` disables that. This is a client-owned repository under a delivery contract, and tooling does not get to add files to it as a side effect of running a server.

## C-D090 - Safety first, then client rules, then service levels

The rule engine runs in a fixed order. The MVP guardrails decide first, and they are imported rather than reimplemented. Client rules route next. Service-level targets are computed last, against the scope the rules produced. The final route uses the graph's exact precedence, and a parametrized test compares it with `app.graph.decide` so the two cannot drift apart.

## C-D091 - Client rules can only narrow the route

A client rule may send a conversation to a person. It can never send one to the model. After the base decision is made, the rule layer can move it only toward a person. No rule action can set the decision, a reply, or the rule codes. Rules can read the safety codes, so a client can route threats to a named team, but they cannot remove them. VIP status is not a safety exemption.

## C-D092 - Rules are data in a closed language

A rule matches on 10 named facts with 7 operators and can set 6 named outcomes. There are no expressions, templates, or regular expressions. A support manager must be able to route mail without anyone being able to express "run this". Anything outside the language is refused at publish time.

## C-D093 - Rules are versioned, never edited in place

Publishing closes the current version's effective window and inserts the next version. Evaluation uses only the versions in force at the moment being decided, so "why was this routed here in March" stays answerable. Publishing behind a version that is already scheduled is refused, because the scheduled version cannot be closed before it opens.

## C-D094 - First rule wins per field; equal-priority disagreement is a conflict

Rules run in ascending priority, with name and version as tie-breakers, so row order never matters. The first rule to set a field wins it, and later rules may still set other fields. Two equal-priority rules that set the same field to different values are a conflict. The engine does not pick one: it records the conflict and routes to a person.

## C-D095 - Unresolvable configuration fails toward a person

A routing conflict, a stored rule that no longer parses, or a rule that forces escalation blocks both auto-resolve and asking the customer, since a clarifying question is also an outward automatic action. A tie between service-level policies blocks auto-resolve and sets no target, but still allows a clarifying question: the tie is a timing problem, not uncertainty about the customer. A conversation with no applicable policy is not escalated. It has no target and says so.

## C-D096 - The most specific SLA policy wins; ties are conflicts

A queue outweighs a department, which outweighs urgency, and a policy that names more scopes outweighs one that names fewer. Two equally specific policies are reported as a conflict, not resolved by row order.

## C-D097 - Working-time arithmetic happens in UTC, anchored to local wall clock

Opening and closing times are local wall-clock times in the policy's zone. All subtraction, addition, and comparison happens in UTC, because Python compares datetimes that share a tzinfo by wall clock and ignores their offsets. An opening time that falls in a spring-forward gap moves to the first real instant after it. Naive timestamps and unknown zones are refused. Holidays are chosen after routing, because the department that observes them is known only then.

## C-D098 - One gateway, and a fixed order of refusals

Everything that reaches a model goes through `AIGateway`, which decides in one order: the model must be approved for this operation in the configured region; the worst-case cost must be reserved against the monthly budget; the key must be readable from Secret Manager; then the call, then structure validation, then fallback. An unapproved model is never called, and a call that cannot be paid for is never made.

## C-D099 - Approvals and prices are client inputs, not constants

`ai_model_approvals` records provider, model, operation, region, and the prices the client agreed, in minor units per million tokens. Provider price lists change, and a budget enforced against a stale built-in list is not enforced. A model without a current approval is refused before any request is built.

## C-D100 - No budget means no calls

A provider whose monthly budget is unset refuses every call with `BUDGET_NOT_CONFIGURED`. The alternative - treating "unset" as "unlimited" - makes the first month's invoice the only control. Budgets are enforced by reserving each call's worst-case cost in the same statement that checks it, so concurrent calls cannot both spend the same headroom, and money is counted in micro-units so a fraction-of-a-cent call is never rounded to zero.

## C-D101 - The ledger is never held across a provider call

Store operations each run in their own short transaction. Reserving inside the request transaction would hold the ledger row locked for the length of a model call, serialising every AI call for that provider and holding a database connection for a minute. Reserve and commit, call, then settle. A live test asserts the row is lockable from outside while calls are in flight.

## C-D102 - Unknown outcomes are charged, refusals are not

A provider does not bill a request it refused, so a rate limit or a rejected key costs nothing. A request that timed out, crashed in an unknown way, or was abandoned by a dead worker may well have been processed, so its reservation is charged. Missing usage numbers are charged at the reservation. The bias is deliberate: over-counting spend pauses work, under-counting exceeds the client's budget.

## C-D103 - Fallback is for availability only, and never leaves the region

A rate limit, an outage, or a timeout may move to the next approved model. A credential, permission, quota, budget, policy, or output failure may not: those need a person, and moving on would hide them or spend money the client did not choose to spend. A fallback must be approved for the same operation in the same region (CLIENT_SCOPE: no cross-region model fallback), and embeddings never fall back at all, because vectors from two models share a length but not a meaning (C-D039). Every fallback, and every rejected candidate, is recorded.

## C-D104 - A failure is a code, never the provider's words

Provider error text is discarded at the boundary. OpenAI's rejection of a key quotes part of that key; an httpx error holds the request and its bearer token; a decode error holds the raw secret bytes. The gateway keeps a stable code, a status, and a retry hint. Nothing is raised inside an `except` block either, because that leaves the caught object attached as `__context__` where an error reporter can find it - an AST test enforces this, and the same defect was fixed in C06's Google client.

## C-D105 - A key is proved before it is stored

Replacing the provider key verifies the candidate against every configured model first - retrieving each model, which generates nothing - and writes the new Secret Manager version only if all of them pass. A mistyped key therefore cannot take the pilot offline. The runtime holds `secretVersionAdder` on that one secret: it may add a version, never read, disable, or destroy one. A successful check clears only the failures it actually proves fixed; budget, quota, and rate-limit problems stay visible.

## C-D106 - The proven prompts run unchanged through the gateway

`GatewayProvider` subclasses the MVP's `OpenAIProvider` and replaces only the client object the prompts are sent through, the same seam the MVP's own tests use. The classification and grounded-drafting prompts, the evidence-key handling, and the repair loop are the verified ones. A gateway failure surfaces as `MODEL_ERROR`, which C09's engine routes to a person.

## C-D107 - Validation errors never repeat the request

FastAPI's default handler echoes the rejected input, and these request bodies carry API keys and OAuth authorization codes. The service returns the location and the kind of problem only. This closes the same hole for C06's callback endpoint.

## C-D108 - The proven workflow runs; only its seams change

C11 compiles and runs the MVP's LangGraph workflow rather than reimplementing it. Its two seams are filled with production parts: the provider is C10's gateway, and the knowledge client is C05's corpus across the MCP boundary. The prompts, the node order, and the grounding validator are the verified ones, so what C11 changes is where knowledge comes from, which model answers, and where the result is written - not how a conversation is decided.

## C-D109 - Two decisions from the same evidence, and the cautious one stands

The workflow decides a route, and C09's rules decide one independently from the same evidence. The pipeline keeps whichever is more cautious. With no client rules the two must agree, and a test asserts that on every labelled case; where they differ, a client rule explains it. A draft written before the rules narrowed the route is withheld rather than stored against a human route.

## C-D110 - The knowledge boundary stays a boundary

Production retrieval is served across MCP stdio, not called as a function. That is what makes "the model sees only retrieved evidence" structural rather than a convention: the tool returns passages, and a draft's support quote must be a substring of the passage text. The client keeps one server process and replaces it when a call fails, because a worker cannot pay process start-up per conversation, and a broken session must not be handed to the next one. A knowledge failure is never an empty result list: it is `MCP_UNAVAILABLE` and a person.

## C-D111 - A triage run is one transaction, keyed by correlation id

The run, its action, the routing it applied, and its service-level targets are one fact about the conversation and are written together. The correlation id is the idempotency key - unique per organization in C04 - so a retried job updates its own row instead of forking the conversation's history. The same id labels the provider calls, so cost joins back to the run without another table.

## C-D112 - The pipeline does not raise for an expected failure

A provider outage, an unreadable key, an exhausted budget, a knowledge outage, or an unparseable answer all end in a stored run whose route is a person, with the reason in its codes. A pipeline that throws leaves the conversation with no record of why nothing happened, which is the one outcome an operator cannot act on.

## C-D113 - Quality is six gates, not an average

Classification, retrieval, groundedness, safety, latency, and cost are measured separately against versioned thresholds, because a support pipeline fails in those six ways independently and a single score hides all of them. A metric that could not be computed fails its gate and is stored as `-1`: a missing row would read as "not measured" or, worse, as a pass. Every gate is deliberately broken in the tests, because a gate that cannot fail measures nothing.

## C-D114 - Every Quality Check records the corpus it measured

A run stores the knowledge fingerprint and article count it ran against, and the workspace compares that with the corpus now. A changed corpus does not fail the run - it means the numbers describe something that no longer exists, which is a quieter kind of wrong and has to be said out loud.

## C-D115 - A live measurement is authorized spend, not a page load

The Quality Check and the demonstration run offline by default, against the labelled dataset, the fixture corpus, and the offline provider, so CI can run them on every change. Measuring the client's own corpus with the client's own model calls the paid provider for every case, so `--live` refuses without `--i-have-client-authorization`, and the workspace page is read-only.

## C-D116 - One service for every human decision

Edit, approve, reject, reroute, assign and resolve go through one service in one order: replay an idempotency key, load the conversation only if this person may act on it, check the permission that decision needs, validate, apply under optimistic locking, and only then touch the provider. Six endpoints would have produced six slightly different answers to "who may do this, against which version, and what was recorded".

## C-D117 - The model's answer is revision 1 and is never overwritten

A human edit is a new revision with its author; `draft_revisions` is unique on (ticket, revision) and a check constraint refuses a human revision without an author or a model revision numbered anything but 1. "What did the model actually write" has to stay answerable after someone improves it.

Approving the model's own words requires the grounding the validator checked. Approving your own words does not: that is your accountability, and the revision records whose words they are.

## C-D118 - A decision names the version it was made against

Every decision carries the ticket version the person was shown, and the update applies only at that version. Two reviewers acting on one view produce one change and one visible conflict. A stale view is reported as a conflict before any other refusal, because every other refusal could be an artefact of a view that has since changed. Losing attempts are recorded, so "two people decided at once" is visible afterwards.

## C-D119 - Refusals are recorded, not just returned

A wrong role, a missing reason, a stale version, a closed conversation: each is written as a `REJECTED` action with its code and a `DENIED` audit event. A refusal that leaves no trace makes "nothing happened" indistinguishable from "somebody tried something they should not have". The one exception is a conversation the person may not act on, which writes only an audit event, because an action row would have to reference a ticket they must not learn exists.

## C-D120 - One rendered form is one decision

The idempotency key is derived from the decision pressed and a nonce minted with the page, so a double submission replays the first outcome - provider result included - while Save and then Approve from the same form remain two decisions. A hidden decision field alongside a submit button of the same name is forbidden: both post a value, the first wins, and that made Approve record an edit.

## C-D121 - The provider draft is claimed before the provider is called

An approval reserves one draft row, commits, calls the provider, then settles. A unique index allows one live draft per conversation and one per idempotency key, so a retry cannot leave two drafts in the client's mailbox. A claim whose worker died is closed as `DRAFT_OUTCOME_UNKNOWN` rather than reused, because whether the provider acted is genuinely unknown.

## C-D122 - Drafting is a client-authorized scope change, not a setting

The Gmail draft path is written and tested, and it cannot run: C06 connects read-only, its scope policy refuses write-capable scopes, and its migration refuses to *store* a compose-scoped credential. Enabling drafts means re-consenting the mailbox and a migration that relaxes that constraint - a reviewable act with the client's approval (C-D011), not a configuration flag. Until then an approval is recorded and reports `DRAFT_SCOPE_NOT_GRANTED`.

`gmail.compose` also permits sending. The product never calls a send endpoint, a test asserts that for every file after stripping comments, and `organizations.sending_enabled` stays constrained to false.

**Amended 2026-10-05.** The client authorized the change. The scope is now askable under a profile and storable under a narrowed constraint; see C-D125 to C-D128. What this entry decided still holds: it took an authorization and a migration, not a flag.

## C-D123 - A draft is addressed, never guessed

The preview is what gets created: the same recipient, subject, body and thread, built from the stored inbound message. With no usable customer address there is no draft, because a reply to a guess is worse than no reply. Outbound headers are sanitised the way C07 sanitised inbound ones - a newline in a subject is a header injection - and nothing is added to the body the client did not ask for. Other recipients on the original are reported to the reviewer rather than copied in by default.

## C-D124 - Only a verified answer pre-fills the reply

An escalation note is internal. A reply box pre-filled with "Triggered controls: HIGH_URGENCY, ANGRY_CUSTOMER" invites someone to adjust a word and send control codes to a customer. The box is pre-filled only from a grounded, cited candidate; otherwise it is empty and says that anything written there is the reviewer's own.

## C-D125 - The scope profile is the deployment's one switch, and it defaults closed

`RESOLVEFLOW_MAILBOX_SCOPES` selects `read_only` (the default) or `read_and_draft`. It decides what the consent screen asks for, what a grant may carry, and whether the review service is given a transport at all. Anything unreadable - a typo, an empty value, a stale name - resolves to `read_only`, because a misconfiguration must narrow access, never widen it. There is no per-organization toggle and no runtime override: widening what a client's mailbox credential may hold is an authorized change to the deployment (C-D011), and a database constraint has to move with it.

## C-D126 - Compose is storable; sending is not, at the database

Migration 0010 narrows C06's `no_write_scope_in_v1` to `no_send_capable_scope`: `gmail.compose` may be stored, and `gmail.send`, `gmail.modify`, `gmail.insert`, `gmail.labels`, the two `gmail.settings` scopes and full mail access still cannot. The narrower constraint is added before the broader one is dropped, so no instant in the chain permits a send-capable credential, and a test asserts the migration's list equals `NEVER_PERMITTED_SCOPES` so policy and schema cannot drift. The downgrade restores C06's text without deleting rows: a compose-scoped credential is a mailbox someone consented to, and a migration is not the place to revoke it.

`gmail.compose` does permit sending at Google's end. That is why the no-send guarantees are structural rather than scope-shaped: no send endpoint anywhere in the tree, `organizations.sending_enabled` constrained to false, and no column in `provider_drafts` that could represent a send.

## C-D127 - Compose is optional, so unticking it connects the mailbox read-only

Google's granular consent lets a person untick one box. A grant missing an optional scope is accepted and turns off the capability that needed it; a grant missing `gmail.readonly` is still a refusal, because the product cannot work without it. The same asymmetry holds at refresh: compose disappearing later leaves the mailbox connected and drafting off, while the mailbox scope disappearing withdraws access as before. Any scope the profile does not permit still revokes the grant on the spot.

## C-D128 - The access token for a draft is leased, never stored

Creating a draft needs a bearer token. The refresh token stays sealed in C06's vault and is never handled by the review path; `RefreshingTokenProvider` asks `ConnectionService.refresh`, holds the access token in memory for 45 minutes against Google's hour, and is unprintable so it cannot reach a traceback or a log aggregator (C-D009). It does not re-decide C06's refusals - each already marks the mailbox degraded or withdrawn - it passes the code on. A refresh opens its own short transaction that closes before the Gmail call, so a slow provider never holds a row lock. A success carrying no token is treated as a failure rather than handed on as an empty bearer.

## C-D129 - Untrusted content is fenced with a token it cannot guess

Every prompt that embeds a customer's words, an uploaded article, or a model's own malformed output wraps them in a delimiter carrying a per-call random token, states that the block is data, and has any forged delimiter stripped before wrapping. An AST test holds this for every prompt site, including the dict-literal form the SDK transport uses, so a new call site cannot skip it.

What bounds the damage is not the prompt but the closed output: classification is validated against a fixed enum, an answer must quote its evidence verbatim, and the route is re-decided by deterministic rules that can only narrow it. Detection of injection phrasing exists for telemetry and the human-facing guardrail signal, and is deliberately never used to refuse a call - a filter with false negatives by construction must not be a gate.

## C-D130 - Redaction keeps what an operator needs

Diagnostics are redacted by mapping key as well as by value, so a credential under an unexpected field name is still removed. The opposite failure is treated as equally real: UUIDs, ticket references, timestamps, status codes, scope URLs and secret *names* survive, and a long number that fails a Luhn check is left alone. A log line that says nothing is not a safer log line - it is an unusable one, and unusable logs get switched off.

Data-subject exports deliberately do **not** pass through redaction. A person asking for their own data is entitled to it; what is redacted there is other people.

## C-D131 - Only CLEAN attachments are readable, and no scanner means FAILED

C04's schema promised a scan gate and deferred the scanner to C13. The gate is now one function where exactly one state passes. `PENDING` does not: "not yet known to be bad" is not "known to be good", so a verdict that never arrives fails closed. An unconfigured deployment gets a scanner that returns `FAILED`, never `CLEAN`; a verdict cannot overwrite an earlier `INFECTED`; and a `CLEAN` verdict must name the bytes it judged.

No code path fetches attachment bytes, so the gate is currently unreachable - asserted by a scan with a positive control. It was written first so that the day a download is added, the test fails until it routes through the gate.

## C-D132 - Rate limits fail closed, and the counter table holds no identifier

Seven named policies, each with a stated reason, enforced on every mutating route. Counting is a single atomic upsert: a read-then-write limiter does not limit anything under the only condition that matters. Keys are stored as SHA-256, so a control that protects the system does not become another store of personal data and its rows fall outside an erasure request. Windows are clock-aligned so instances agree without coordinating.

Failing closed costs nothing, because every limited action already needs the same database. The two refusals are distinguishable - `RATE_LIMITED` and `RATE_LIMIT_UNAVAILABLE` - because an operator must tell a busy client from a broken counter.

Mutations happen in two tiers, so the limiter exists twice over one table and one policy list, with a test asserting the copies agree. No constraint relates the window to a database-generated timestamp: the window comes from the application's clock, and a few seconds of skew would make a fail-closed limiter refuse legitimate work.

## C-D133 - A strict content-security policy, and what it does not cover

`default-src 'none'` with a per-request nonce and `strict-dynamic`, plus HSTS for two years. The application loads no external script, font, image or iframe and uses no inline style attribute, so no `unsafe-*` allowance was needed. Verified in a browser, with negative controls: a cross-origin fetch and image were blocked with violation events, and a parser-inserted inline script did not execute.

`strict-dynamic` deliberately trusts scripts an already-running script creates. The policy therefore constrains injected **markup**, which is the common vector, and does not contain an attacker already executing JavaScript. Removing it would break the framework's own chunk loading. A consequence accepted: pages carrying a nonce cannot be prerendered, so three pages are now rendered per request, and the framework's own 404 page stays static with its scripts blocked.

## C-D134 - Erasure destroys content and keeps the record

A data-subject erasure keeps every row and scrubs the personal fields across the eight tables that hold one, replaces addresses with a reserved `.invalid` address, and recomputes the draft checksum rather than leaving it pointing at vanished text. Deleting the rows would take the decision history with them - who reviewed what, when, and why - which the client needs and the audit trail must retain.

It refuses while a legal hold is active: a hold exists to stop data being destroyed, and honouring an erasure over one is the more serious failure. It is Owner-only, organization-scoped, idempotent, and recorded in an append-only row identified by a digest rather than the address. Two things it cannot do are named in that record rather than assumed: the attachment objects in the bucket, and any backup taken before it ran.

## C-D135 - Audit rows carry no customer content, which is what makes them keepable

`audit_events` refuses UPDATE and DELETE by trigger, so anything written there outlives an erasure request and cannot be removed. Rather than weaken the trigger, the premise is enforced: audit metadata carries staff actors, operational codes, identifiers and counts, and an AST test asserts that no writer passes a content field. The erasure workflow does not touch the audit trail, and a test asserts that too.

What audit rows do retain is the **staff** member's email. How long that is kept is a legitimate-interest and legal-obligation question for the client, recorded in `docs/PRIVACY.md` rather than decided in code.

## C-D136 - CI calls the Makefile, because three copies of a list is how it drifted

CI carried its own inline copy of the lint and type-check paths. The Makefile's list advanced through C09 to C12 and CI's did not, so the rules engine, the AI gateway, the triage pipeline and the review service were unchecked in CI for four phases while every local run passed. CI now calls `make python-check`, the Makefile states its paths once, and `tests` is linted whole so a new test file cannot escape. Four tests assert this, naming the defect they prevent.

Scanning is split by what ships: `pip-audit` and `npm audit --omit=dev` block the build, while advisories in build-time packages are reported without blocking - they do not reach the image, and their only published fix downgrades the linter below the framework version. The secret-scan allowlist names files, never patterns: an allowlisted pattern would hide a real secret anywhere it appeared.

## C-D137 - Two pilot stages, and the ladder stops

`OBSERVE` reads and triages and writes nothing to the mailbox; `DRAFT` also creates a draft a person sends themselves. The switch is C13's scope profile, so there is one mechanism rather than a second mode flag. Leaving `OBSERVE` requires six conditions agreed in advance, carried as a list in `app/pilot.py` and mirrored in `docs/UAT_CRITERIA.md`, with a test asserting the two cannot drift - an advance nobody agreed beforehand becomes an argument afterwards.

There is no third stage. Sending is not a later rung; it is absent from the product, and `automatic_sending` stays a published constant rather than a setting.

`observe_mode` was previously reported to callers as a literal `true`. That became untrue the moment C12a made drafting possible, so it is now derived from the posture and a UAT step can confirm it from outside.

## C-D138 - The pilot measures what it can, and says what it cannot

Offline the provider is a deterministic keyword matcher and the labelled fixture cases are the ones it was built against, so its reported accuracy of 1.00 is true by construction. It demonstrates that the measurement harness works end to end and predicts nothing about a real model on real mail, and `docs/AI_PERFORMANCE_REPORT.md` says so rather than quoting the figure as a result.

What is measured instead needs no ground truth and is real: over 45 realistic conversations, that nothing is auto-resolved without validated grounding and a citation, that a guardrail conversation is never auto-resolved, that every citation exists in the corpus searched, that routes are reproducible across passes, and the route mix, latency and cost accounting. Accuracy against the client's convention requires the client's labelled cases, which is an explicit precondition of the pilot rather than something to be substituted. Labelling them ourselves would measure agreement with our own judgement.

## C-D139 - A capability is scanned by identifier, never by prose

The acceptance run's "nothing sent" invariant first searched each serialized trace step for "send" and failed on two conversations, because the knowledge-search step carries the query and a customer had written "please send me another link". Scanning prose for a capability finds the customer's words, not the product's behaviour - the same mistake C12 made with its send scan.

The check now reads step and tool *names* only, and splits each identifier into words before matching, because `\bsend\b` matches neither `send_reply` nor `sendMessage`. A positive control asserts a real send step is caught; `draft_reply` and `sender_address` are deliberately not.

## C-D140 - The smoke test is read-only, and never calls a paid model

A post-deploy check that writes to production leaves rows nobody asked for, and on a day when something is already wrong it makes the state harder to read. Every statement is a `SELECT`, every request is a `GET`, and tests assert both - "read-only" is a property of that list and nothing else enforces it.

It also never calls a model. Verifying a provider costs money, and doing it on every deploy would make releases quietly expensive; the gateway's own verification exists for when an operator wants it deliberately.

The expected schema revision is tied by a test to the migration chain's head, so the check cannot silently pass a deployment that is behind.

## C-D141 - A check that follows redirects cannot tell refusal from service

The first workspace check accepted any of `{200, 302, 303, 307}` and reported a pass on `200`. urllib follows redirects by default, so it had followed the redirect to the sign-in page and reported that page's status - indistinguishable from the workspace being served to a visitor with no session, which is the failure the check exists to find.

It now stops at the redirect, requires a 3xx whose `Location` points at the sign-in page, and separately asserts that no workspace markup came back. An accepted-status set wide enough to include both the pass and the failure is not a check.

## C-D142 - An access review separates what is wrong from what needs deciding

Findings - a revoked member with a live session, a permission held by an inactive member, an account with no membership, no allowlisted domain, no active Owner - are wrong now and fail the run. Observations - six Owners, a dormant account, an agent with no mailbox permission - need a human to decide, and are reported without failing.

Reporting a judgement call as a failure trains an administrator to ignore the output, which is worse than not running the review at all. Organization-wide roles are deliberately not flagged for holding no mailbox permission: they see everything by role, so a permission would be redundant and the flag would be noise.

## C-D143 - The handover is a checklist with empty signatures, not a statement of readiness

Twenty-eight rows across deployment, recovery, monitoring, cost, access, audit, privacy and mail, each needing a date and an initial; four training sessions that end with the person doing the thing rather than watching it; and the rollback and support boundaries in writing, because "we assumed you would handle that" is the most expensive sentence in a handover.

Every signature block is empty and a test asserts they stay that way. A pre-filled signature would make an unsigned handover look signed.

The limitations are listed in the handover itself rather than left in a phase report: no attachment is read, no scanner is integrated, prompt injection can shift a classification, build-time advisories remain, erasure cannot reach backups or the bucket, and no penetration test has been performed.

## C-D144 - A gate that depends on a running system is not green because its tooling exists

C15's gate is production smoke tests, alerts, audit, backups, budgets, access review and handover. Each is a property of a deployed system. The tools to verify them are built and exercised against a live local stack; the gate remains **not met**, and the report says so in its first line rather than claiming readiness because the checklist exists.

Six of C15's eight activities are blocked on the client's authorization, project, billing, Workspace administrator and people. C14's gate is also not green - no pilot has run - so C15 cannot meaningfully precede it.
