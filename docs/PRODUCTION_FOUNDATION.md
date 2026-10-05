# Production Repository Foundation

## Repository layout

```text
apps/web/                  Next.js App Router customer-facing application
apps/web/src/lib/authz/    Framework-free authorization core (roles, decisions)
apps/web/src/lib/identity/ Session resolution and the identity repository port
apps/web/src/lib/auth/     Auth.js configuration and server-side access guards
app/api/                   Private FastAPI boundary for Python capabilities
infra/terraform/           Client-owned GCP definition (C02); never applied here
scripts/bootstrap_organization.py  One-time single-organization Owner seed
app/retention.py           Retention sweep planning and hold-aware executor
app/knowledge/             Ingestion, chunking, embeddings, hybrid retrieval
scripts/compare_retrieval.py  Labelled MVP-vs-pgvector retrieval comparison
app/mailbox/               Gmail connection, sealed credentials, mailbox access
app/ingestion/             Watches, history sync, normalization, recovery
apps/web/src/lib/workspace/  Inbox filters, state derivation, scoped queries
apps/web/src/app/workspace/  Overview, inbox, ticket workspace
app/                       Existing triage, guardrail, RAG, MCP, and evaluation core
alembic.ini               Credential-safe migration configuration
migrations/               PostgreSQL-ready chain; C04 adds durable schemas
contracts/openapi/v1.json  Generated and versioned service contract
tests/                     Python behavior and API contract tests
apps/web/tests/            Web-side contract checks
scripts/export_openapi.py  Deterministic contract exporter
Dockerfile.api             Private Python API container
apps/web/Dockerfile        Standalone Next.js container
compose.client.yml         Local production-shape orchestration
Dockerfile                 Preserved Streamlit MVP/reference container
```

The repository is one product, not two rewrites. The production web app reaches Python behavior through the versioned API. Streamlit may still import Python modules directly because it is a temporary internal regression and evaluation reference.

## Service boundaries

```text
Browser
  -> Next.js web/BFF
       -> private /v1 FastAPI service
            -> existing LangGraph workflow
                 -> MCP knowledge tool
                      -> current local RAG during C01
```

The browser must never call Python directly. Protected Python endpoints require an internal bearer token plus organization, actor, and request headers.

Since C03 that context is real: the BFF builds it from an `AuthContext` derived from the database on each request, and forwards nothing from the incoming browser request. The Python service has no database and does not re-check membership - it authenticates the calling service and refuses malformed or unknown context (an unrecognised role is a 400). Being reachable only from inside the VPC is what makes that sufficient.

The API accepts no organization identifier in its body. Unknown request fields are rejected, and the contract proves no sending endpoint exists.

## Versioned contract

- API version: `v1`
- Generated source: FastAPI OpenAPI model
- Artifact: `contracts/openapi/v1.json`
- Python drift test: `tests/test_api_contracts.py`
- Web contract test: `apps/web/tests/contracts.test.mjs`
- Regenerate: `make contract`

Contract changes must update server models, regenerate OpenAPI, update consumers/tests, and receive review. Do not hand-edit generated JSON.

## Local development

Prerequisites: Python 3.11+ (CI uses 3.14), Node from `.nvmrc`, and optionally Docker.

```bash
nvm use
npm ci

# Terminal 1
make api

# Terminal 2
make web
```

The web health route checks Python `/healthz` without exposing internal credentials.

```bash
docker compose -f compose.client.yml up --build
```

- Next.js: http://127.0.0.1:3000
- Python health: http://127.0.0.1:8080/healthz
- Local FastAPI docs: http://127.0.0.1:8080/docs

The Compose token default is local-only. Real environments inject a random Secret Manager value. No browser-visible variable may contain it.

## Verification

```bash
make check
```

Infrastructure is verified separately and offline, because C02 defines the
client's GCP environments without applying them:

```bash
make infra-check
```

See `infra/docs/INFRASTRUCTURE.md` for the target topology and the client inputs
that gate an apply, and `infra/docs/RUNBOOK.md` for apply, secret injection,
deploy, rollback, and restore procedures.

For a fresh Python environment, install `requirements-dev.txt`; production dependencies remain in `requirements.txt`. CI verifies contract drift, all Python tests, Ruff, mypy, web contract tests, ESLint, TypeScript, the production Next.js build, and both service containers. It does not deploy.

The exact C01 evidence is recorded in `CLIENT_C01_TEST_REPORT.md`.

## Authentication and authorization (C03)

Google Workspace sign-in through Auth.js with database sessions. Two conditions
must both hold: the email domain is on the organization's approved list, and an
invited membership exists. A matching domain alone never grants access.

Every protected page and route handler calls `requirePageAccess` or
`requireAccess`. These re-derive identity from the database on each request, so
revoking a membership takes effect immediately rather than when a token expires.
Role, organization, and department are never read from a cookie, header, query
parameter, or request body.

The middleware is a filter, not the authorization boundary: it rejects requests
with no session cookie and sets security headers, and makes no role decision
because it has no database access at the edge.

## Mailbox connection (C06)

Read-only Gmail connection for explicitly named shared mailboxes. An Owner or
Admin starts the flow; Google's consent screen returns to
`/api/mailboxes/oauth/callback`, which forwards only the state and code to the
internal API. The internal API exchanges the code, then refuses and revokes the
grant unless the scopes are exactly read-only and the Gmail profile is the
mailbox being connected. Accepted refresh tokens are sealed with AES-256-GCM,
bound to their organization and mailbox, and never stored as plaintext;
access tokens are never stored at all.

| Route | Purpose |
|---|---|
| `POST /api/mailboxes/{id}/connect` | Start; redirects only to `accounts.google.com` |
| `GET /api/mailboxes/oauth/callback` | Complete; never reflects its query string |
| `DELETE /api/mailboxes/{id}/connection` | Revoke at Google and destroy the local credential |

Without `GMAIL_OAUTH_*` and `MAILBOX_TOKEN_ENCRYPTION_KEY` configured, the
internal endpoints answer 503. Connection modes, the Internal-app setup, and who
may connect what are in `docs/MAILBOX_CONNECTION_MODES.md`; keyring creation and
rotation are in `infra/docs/RUNBOOK.md`.

## Operations workspace (C08)

Three surfaces: an overview of where attention is needed, a unified inbox with
seven saved views, and the ticket workspace.

Visibility is one SQL predicate - organization, mailbox permission, department -
applied to every workspace query, mirroring the authorization core. A
conversation you may not see answers 404, identical to one that does not exist.

Queue state lives in the URL, so a filtered view can be linked and bookmarked
and the back button works. Filters are a plain GET form and assignment is a
server action, so both work without client JavaScript; keyboard shortcuts and
the pausable polling refresh are accelerators on top.

"New to me" is derived per person by comparing read position against the
thread's latest message. No unread flag is stored, and Gmail's shared UNREAD
label is not used - it cannot answer a per-person question.

Colour is a checked contract: every text colour is declared as a
`--ws-<role>-fg` / `--ws-<role>-bg` pair in `globals.css`, and
`apps/web/tests/contrast.test.mjs` applies the WCAG formula to each pair on
every commit.

## Mailbox ingestion (C07)

Gmail pushes a notification to Pub/Sub, which delivers it to the private worker
endpoint with an OIDC token. The notification says only "this mailbox changed":
every sync reads from the mailbox's own stored history cursor, so duplicated,
delayed, and out-of-order notifications all converge on the same result.

```text
Gmail watch -> Pub/Sub -> worker
  -> one job per Pub/Sub message id (deduplicated)
  -> history.list from the stored cursor
  -> messages.get for each new id
  -> normalize (untrusted input)
  -> threads, messages, attachments, tickets (idempotent upserts)
  -> cursor advances only after the window succeeded
```

Recovery rules, all verified against PostgreSQL:

| Situation | Behaviour |
|---|---|
| Notification redelivered | Deduplicated by Pub/Sub message id; one job |
| Window reprocessed after a crash | No duplicate rows; the cursor never rewinds |
| Cursor older than Gmail's retained history | Reconcile by listing messages, then a fresh watch supplies the cursor |
| Watch near expiry | Renewed on a two-day threshold against a seven-day expiry |
| Quota exceeded | Deferred with bounded exponential backoff; dead-lettered after 10 attempts |
| Grant revoked | Mailbox stops; the job completes rather than retrying forever |
| Worker crash | The lease expires and the job returns to the queue |

Attachments are classified but never downloaded here: anything outside the
approved type list, oversized, or empty is recorded as SKIPPED. Access tokens
are cached in memory for 45 minutes and never persisted.

## Knowledge and retrieval (C05)

Approved sources are PDF, Markdown, text, HTML, and CSV. Each is validated,
extracted, normalized once, chunked with exact character offsets, embedded, and
indexed. Retrieval is pgvector cosine plus PostgreSQL full text, scoped by
organization and publication status in SQL, reranked with the proven MVP
formula, and cited by exact passage offsets.

```bash
# Compare the pgvector path against the proven MVP path on labelled cases.
RESOLVEFLOW_TEST_MODE=1 DATABASE_URL=... make retrieval-check
```

Thresholds are versioned (`c05.2026-09-15`). A change to any rerank weight is a
retrieval change: it must fail the scoring tests and be re-measured, never
merely re-approved.

```bash
# One-time, per environment, by an authorized operator.
python scripts/bootstrap_organization.py \
  --slug acme --name "Acme Support" \
  --domain acme.example --owner-email owner@acme.example
# Re-run with --apply and DATABASE_URL set to write.
```

## Current limitations

- The C01 foundation screen is synthetic and labels disconnected capabilities.
- Member administration screens (invite, change role, revoke) arrive with C08;
  the schema and permissions exist and are enforced.
- Ingestion fills the C04 tables (C07) and the workspace displays them (C08),
  but nothing triages them yet: the production triage graph and quality
  measurement are C11, and ticket actions are C12.
- Routing, SLA computation, and business hours are modelled but not calculated;
  the rule engine is C09.
- No worker service is deployed. The ingestion service is exercised directly;
  wiring it to the Cloud Run worker and its scheduler happens at deployment.
- Connection and ingestion are verified against a simulated Google only. No real
  mailbox has been connected and no request has reached Google.
- Production retrieval runs on pgvector plus PostgreSQL full text (C05). The MCP
  tool still calls the MVP path; moving it is C11.
- Retrieval metrics were measured with deterministic embeddings and must be
  re-measured against real models and the client corpus at C14.
- Retention policies are rows with no client-agreed values yet; the sweep
  defaults to a dry run.
- Gmail connection/ingestion begins in C06-C07.
- API triage uses the existing OpenAI/MCP core when called; tests inject a fake service and make no provider call.
- Do not expose Python publicly. The internal bearer token authenticates the
  calling service; it is not user authentication.
