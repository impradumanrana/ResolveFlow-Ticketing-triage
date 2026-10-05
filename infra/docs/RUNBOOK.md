# Infrastructure Runbook (C02)

Procedures for applying, operating, and rolling back the client-owned
infrastructure. **Nothing in this runbook has been executed.** C02 delivers the
definition; each procedure below runs only after the client supplies its inputs
and explicitly authorizes the step (decision C-D011).

Read [INFRASTRUCTURE.md](INFRASTRUCTURE.md) first.

## Before any apply

Confirm and record, in `DECISIONS.md`:

1. The client GCP project IDs for staging and production, and who administers them.
2. The approved region, and that no cross-region fallback is permitted.
3. The billing account and who owns spend.
4. Named operations contacts for alerts.
5. Written authorization for this specific step.

Never guess any of these. An unresolved input blocks the phase that consumes it,
not the phases before it.

## One-time bootstrap

The state bucket cannot be created by the configuration that stores state in it.
A named client administrator runs this once.

```bash
cd infra/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars   # fill in; git-ignored
terraform init
terraform plan -out=bootstrap.tfplan           # review every line
terraform apply bootstrap.tfplan
```

Record the bucket name. Store the local `bootstrap` state with the client's
records, or migrate it into the bucket it just created.

## Applying an environment

Staging first. Production only after the staging gate and explicit approval.

```bash
cd infra/terraform/envs/staging
cp backend.hcl.example backend.hcl                 # bucket from bootstrap
cp terraform.tfvars.example terraform.tfvars       # client inputs
terraform init -backend-config=backend.hcl
terraform plan -out=staging.tfplan
```

**Review the plan before applying.** Specifically confirm:

- No resource outside the intended client project.
- Cloud SQL shows no public IP and no authorized networks.
- Only `rf-<env>-web` shows `INGRESS_TRAFFIC_ALL`.
- No secret *value* appears anywhere in the plan.
- Buckets show `public_access_prevention = enforced`.

```bash
terraform apply staging.tfplan
```

The first apply enables APIs, which can take several minutes. A re-run is safe:
the configuration is idempotent.

### Expected first-apply friction

- **Service agents do not exist until their API is enabled.** If a KMS grant
  fails on a missing service agent, re-run apply once the API has settled.
- **Cloud Run services start on a placeholder image** and will not serve the
  product until CI deploys a real image. This is intentional.
- **Secrets are empty.** A service referencing an empty secret fails to start.
  Inject values before the first real deploy.

## Injecting secret values

Terraform never holds a secret value. An authorized client operator adds each
version directly, and the value never passes through this repository, a CI log,
a pull request, or a chat message.

```bash
# Per secret, from an authorized workstation:
printf '%s' "$VALUE" | gcloud secrets versions add rf-staging-llm-provider-api-key \
  --project="$CLIENT_PROJECT" --data-file=-
```

Secrets to populate before the first real deploy:

| Secret | Value | Owner |
|---|---|---|
| `rf-<env>-auth-session-secret` | Random 32+ byte session signing key | Delivery team |
| `rf-<env>-google-oauth-client-secret` | Workspace OAuth client secret | Client Workspace admin |
| `rf-<env>-gmail-oauth-client-secret` | Gmail connection OAuth client secret, read by `api` (code exchange) and `worker` (refresh). A separate client from sign-in | Client Workspace admin |
| `rf-<env>-mailbox-token-encryption-key` | Keyring for sealing mailbox refresh tokens, formatted `key-id:base64-32-bytes[,older-key-id:base64]` (C06) | Delivery team |
| `rf-<env>-llm-provider-api-key` | Client BYOK provider key | Client |
| `rf-<env>-internal-service-token` | Random service-to-service token | Delivery team |

Use `printf` rather than `echo`, and never pass a secret as a command argument:
both leak into shell history.

## Replacing the AI provider key

Two ways, both ending in a new Secret Manager version. Prefer the first.

**From the application (C10).** An Owner or Administrator opens **AI provider**
in the workspace and pastes the new key. The service verifies it against the
provider - retrieving each configured model, which generates nothing and costs
nothing - and stores it only if every check passes. A key the provider rejects
is never written, so a typo cannot take the pilot offline. The `api` service
account holds `secretmanager.secretVersionAdder` on this one secret: it may add
a version, and may not read, disable, or destroy one. Afterwards the page shows
only the last four characters.

**Out of band.** The same `gcloud secrets versions add` command as above. Then
open the same page and use **Check the stored key**, so the stored key is known
to work before the next customer conversation depends on it.

Either way, disable the old version once the new one is confirmed:

```bash
gcloud secrets versions disable <version> --secret=rf-<env>-llm-provider-api-key \
  --project="$CLIENT_PROJECT"
```

The service reads the latest version and caches it for five minutes, so a
rotation takes effect without a deploy. A version that is disabled or destroyed
reports `CREDENTIAL_EXPIRED` and routes conversations to a person rather than
failing silently.

## Approving a model, a region, or a budget

The gateway calls nothing that is not approved. Approvals are rows in
`ai_model_approvals`: provider, model, operation, region, and the prices the
client has agreed, in minor currency units per million tokens. The monthly
budget lives on the provider's `ai_configs` row.

Consequences worth stating before the pilot:

* No budget means no calls. A provider with `monthly_budget_minor_units` unset
  refuses every call with `BUDGET_NOT_CONFIGURED`.
* An unapproved model is never called, even when it is configured.
* Prices are the client's, not a built-in list. A stale price makes the budget
  wrong, so they are reviewed when the provider changes its pricing.
* Changing the embedding model is a migration and a reindex, not an edit
  (C-D039).

## Creating the mailbox token keyring

Generate one 32-byte key with an identifier that sorts by date, and store it as
a new secret version. The value never passes through this repository, a chat
message, or a CI log.

```bash
printf 'mbx-%s:%s' "$(date -u +%Y%m)" "$(openssl rand -base64 32)" \
  | gcloud secrets versions add rf-<env>-mailbox-token-encryption-key \
      --project="$CLIENT_PROJECT" --data-file=-
```

Without this secret, mailbox connection answers 503. It does not fall back to
storing anything unencrypted.

## Rotating the mailbox token key

Every stored credential records the key id that sealed it, so rotation is
additive and never locks anyone out.

1. Add a new version whose value is the **new key first**, then the current key:
   `mbx-202610:<new>,mbx-202609:<current>`. New connections seal with the new key;
   existing credentials still open with the old one.
2. Deploy, then reseal existing credentials under the new key (`TokenVault.reseal`)
   and confirm none remain on the old id:
   `SELECT key_id, count(*) FROM mailbox_credentials GROUP BY key_id;`
3. Only when the old id's count is zero, add a version containing just the new key.

Removing a key while any credential still uses it makes those mailboxes
`DEGRADED` with `CREDENTIAL_UNREADABLE`. That is recoverable by restoring the
previous secret version; the credentials are not deleted.

## Turning mailbox drafting on or off

Drafting lets an approved answer become a **draft** in the client's mailbox. It
is never enabled by default, and it takes four things together - any one alone
does nothing:

1. the client's written authorization (it changes their consent screen);
2. `https://www.googleapis.com/auth/gmail.compose` added to **their** OAuth
   client's consent screen in their Google Cloud project;
3. `RESOLVEFLOW_MAILBOX_SCOPES=read_and_draft` on the API service;
4. migration `20260918_0010` applied, and the mailbox **reconnected**.

Order matters. Set the profile and apply the migration before reconnecting, or
consent will ask for a scope the database still refuses to store and the
connection will fail with `WRITE_SCOPE_GRANTED`.

```bash
# After deploying with the profile set and the migration applied:
psql "$DATABASE_URL" -c "SELECT conname FROM pg_constraint
  WHERE conrelid = 'mailbox_credentials'::regclass AND conname LIKE '%scope%';"
# expect: ck_mailbox_credentials_no_send_capable_scope
```

Then an Owner or Admin disconnects and reconnects the mailbox in the product,
signed in **as the shared mailbox**, leaving the compose box ticked. Confirm:

```bash
psql "$DATABASE_URL" -c "SELECT m.email_address, m.status,
  'https://www.googleapis.com/auth/gmail.compose' = ANY(c.granted_scopes) AS can_draft
  FROM mailboxes m LEFT JOIN mailbox_credentials c ON c.mailbox_id = m.id;"
```

`can_draft = false` means the person unticked the box. The mailbox still works
read-only; reconnect to grant it. Nothing is broken and nothing needs undoing.

**Turning it off.** Set the profile back to `read_only`, reconnect the mailbox
so its grant no longer carries compose, *then* downgrade the migration. The
downgrade fails loudly while a compose-scoped credential is still stored - by
design, so a migration never silently revokes a grant someone consented to.
The failure is transactional: schema and credentials are left as they were.

Drafting being on never makes the product send. The no-send guarantees do not
depend on the scope: no send endpoint exists in the codebase,
`organizations.sending_enabled` is constrained to `false`, and `provider_drafts`
has no column that could record a send. If someone asks for sending, that is a
new authorization and a code change, not a configuration flag.

## Answering a data-subject request

Both operations are run by an operator, deliberately, as the Owner. Neither is
in the web interface: an action that cannot be undone should not be one click
away. Background and what to tell the customer: `docs/PRIVACY.md`.

**Export** ("send me my data"):

```bash
python - <<'EOF'
from sqlalchemy import create_engine
from app.privacy import export_subject
import json, os
engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as c:
    bundle = export_subject(c, os.environ["ORGANIZATION_ID"], "customer@example.net")
print(json.dumps(bundle, indent=2))
EOF
```

Review it before sending. Other participants' addresses are redacted by
design; confirm that is what you see.

**Erasure** ("delete my data"):

```bash
python - <<'EOF'
from sqlalchemy import create_engine
from app.privacy import Actor, SubjectRequest, erase_subject
import os
engine = create_engine(os.environ["DATABASE_URL"])
actor = Actor(os.environ["OWNER_MEMBERSHIP_ID"], os.environ["ORGANIZATION_ID"], "OWNER")
with engine.begin() as c:
    outcome = erase_subject(
        c,
        SubjectRequest(os.environ["ORGANIZATION_ID"], "customer@example.net", actor,
                       reason="Article 17 request, ticket REF-123"),
    )
print(outcome.code, outcome.counts)
print("delete these objects from the bucket:", outcome.storage_objects)
EOF
```

Then **delete the named storage objects**. The database row cannot reach the
bucket, so this step is yours:

```bash
gsutil rm gs://<bucket>/<object>        # one per entry in storage_objects
```

Finally, confirm:

```bash
psql "$DATABASE_URL" -c "SELECT subject_digest, counts, completed_at FROM erasure_records ORDER BY completed_at DESC LIMIT 1;"
```

**Refusals and what they mean.** `LEGAL_HOLD_ACTIVE` — a hold is in force;
release it deliberately or decline the request, do not work around it.
`ROLE_LACKS_PERMISSION` — run as the Owner. `ALREADY_ERASED` — nothing to do,
and the earlier record is named. `SUBJECT_NOT_FOUND` — that address is not in
this organization; check the spelling before replying to the customer.

## Running a retention sweep

```bash
# Plan first. Always.
python -m app.retention --organization "$ORGANIZATION_ID" --dry-run
# Then, having read the plan:
python -m app.retention --organization "$ORGANIZATION_ID" --execute
```

A sweep refuses for any data class under a legal hold, and tickets and audit
events are not sweepable at all. If a sweep would delete more than you expect,
stop: the retention policy is probably wrong, not the sweep.

## Turning the pilot stage

The deployment is in one of two stages, and `GET /v1/capabilities` reports
which. There is no third stage; sending is absent from the product.

```bash
curl -s -H "Authorization: Bearer $RESOLVEFLOW_INTERNAL_API_TOKEN" \
     -H "X-ResolveFlow-Organization-Id: $ORGANIZATION_SLUG" \
     -H "X-Request-Id: runbook-check-1" \
     "$AI_API_BASE_URL/v1/capabilities" | jq '{pilot_stage, observe_mode, automatic_sending}'
```

**Observe Mode** (`RESOLVEFLOW_MAILBOX_SCOPES=read_only`, the default) is where
a pilot starts: mail is read and triaged, an approval is recorded, and nothing
is written to the mailbox.

**Draft stage** (`read_and_draft`) requires all six exit criteria in
`docs/UAT_CRITERIA.md` §6 to be met and recorded, and then the procedure in
"Turning mailbox drafting on or off" below. Returning to Observe Mode is the
fastest containment step for anything involving drafts: set the profile back
and redeploy.

## Running the pilot measurements

Offline, on every change, and safe to run anywhere:

```bash
RESOLVEFLOW_TEST_MODE=1 python -m scripts.quality_check        # the eleven gates
RESOLVEFLOW_TEST_MODE=1 python -m scripts.acceptance_run       # safety invariants, route mix
```

Against the client's own corpus, provider and labels — this **spends money**
and refuses without explicit authorization:

```bash
python -m scripts.quality_check --live \
    --database-url "$DATABASE_URL" \
    --organization "$ORGANIZATION_ID" \
    --membership "$MEMBERSHIP_ID"
```

Record both in `docs/AI_PERFORMANCE_REPORT.md`. The offline numbers are harness
checks, not model evidence, and the report says so — do not quote the offline
accuracy figure to the client as a prediction.

## Deploying an image

CI deploys; Terraform does not. The pipeline authenticates through Workload
Identity Federation, pushes to the environment's Artifact Registry, runs the
migration job, then rolls the revision.

```text
build -> push by digest -> execute rf-<env>-migrate -> deploy web/api/worker
```

Migrations run to completion before any new revision serves traffic. A failed
migration must stop the deploy.

## Rollback

**Application rollback** is the common case and does not involve Terraform.

```bash
gcloud run services update-traffic rf-<env>-web \
  --project="$CLIENT_PROJECT" --region="$REGION" --to-revisions=<previous>=100
```

Cloud Run keeps previous revisions, and the services ignore image drift, so a
later `terraform apply` will not undo the rollback.

**A rolled-back application against a migrated database is the dangerous case.**
Migrations must be backward-compatible for one release, so the previous revision
can run against the new schema. If it cannot, the rollback is a restore, not a
traffic shift - see below.

**Infrastructure rollback** is `git revert` of the offending change, then plan
and apply. Terraform destroy is not a rollback mechanism: `prevent_destroy` on
keys, buckets, secrets, and the database will refuse, deliberately.

## Database restore

Targets are proposed, not contractual: RPO 24 hours, RTO 4 hours, pending the
client's business-impact review.

```bash
# Point in time, into a NEW instance. Never restore over the live instance.
gcloud sql instances clone rf-<env>-postgres rf-<env>-postgres-restore \
  --project="$CLIENT_PROJECT" --point-in-time="2026-01-01T12:00:00Z"
```

Then verify the clone, repoint services, and only afterwards decide what happens
to the original. The PITR window is 3 days in staging and 7 in production.

A restore drill in staging is required before the production go/no-go (C14).

## Alert response

| Alert | First check | Likely cause |
|---|---|---|
| Cloud Run 5xx rate | Revision logs, then roll back traffic | Bad deploy, missing secret, database unreachable |
| Pub/Sub backlog age | Worker health and revision status | Worker down, failing handler, expired Gmail watch |
| Dead-lettered messages | The `-dlq-hold` subscription | Unprocessable event; each one is a mailbox event the product missed |
| Cloud SQL disk utilisation | Growth rate against the autoresize limit | Retention not running, or unexpected volume |
| Triage failures | Provider health, MCP availability, retrieval | Provider outage, budget exhaustion, missing knowledge |

Dead letters are held for 7 days for replay. Fix the cause first: replaying into
a broken handler just re-dead-letters.

## Budget response

Budgets notify; they do not cap spend. Capping would take the product down
rather than degrade it, and is a client billing decision. On a budget alert,
check Cloud Run instance counts and provider usage before assuming an attack.

## Teardown

Only on client instruction, with written confirmation, and only after an export
the client has accepted.

`prevent_destroy` guards the database, buckets, secrets, KMS keys, and the state
bucket. Removing those guards is a reviewed change, not an operational step. A
destroyed KMS key makes its ciphertext permanently unreadable - including
backups.
