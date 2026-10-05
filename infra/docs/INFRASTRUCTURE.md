# Client-Owned GCP Infrastructure (C02)

**Status:** Defined, reviewed offline, and **not applied.** No cloud resource
exists from this phase. Applying it requires the client inputs listed in
[Required client inputs](#required-client-inputs) and explicit authorization per
decision C-D011.

## What this defines

Two independent environments in **client-owned** Google Cloud projects:

| | Staging | Production |
|---|---|---|
| Project | Client-supplied, separate | Client-supplied, separate |
| Cloud SQL | `db-custom-1-3840`, ZONAL | `db-custom-2-7680`, REGIONAL |
| PITR window | 3 days | 7 days |
| Retained backups | 7 | 30 |
| Warm instances | 0 (scale to zero) | 1 per service |
| Subnet | `10.60.0.0/20` | `10.70.0.0/20` |
| Web reachability | Named client testers only | `allUsers` at signed launch only |
| Deletion protection | Database only | Database and Cloud Run services |

They share no project, network, database, bucket, key, secret, registry, or
service account. Promotion is a deliberate CI action against a specific image
digest, never shared state.

## Layout

```text
infra/terraform/
  bootstrap/            State bucket. Applied once, by a client administrator.
  envs/staging/         Staging root module.
  envs/production/      Production root module.
  modules/
    environment/        Composes everything below into one environment.
    project_services/   Enables exactly the APIs used.
    network/            VPC, subnet, private service access, Cloud NAT, firewall.
    kms/                Per-data-class CMEKs.
    service_accounts/   Runtime identities and their minimal project roles.
    secrets/            Secret containers and per-secret access. No values.
    artifact_registry/  Per-environment image repository.
    database/           Cloud SQL PostgreSQL, private IP, IAM auth, PITR.
    storage/            Private buckets with CMEK, versioning, lifecycle.
    messaging/          Pub/Sub, dead letters, Cloud Tasks, Cloud Scheduler.
    runtime/            Cloud Run services and the migration job.
    observability/      Log sink, log metric, alert policies, uptime, budget.
    cicd/               Workload Identity Federation and CI identities.
```

## Service topology

```text
                    Browser (client staff, Workspace login)
                              |
                              v
        +---------------------------------------------+
        |  Cloud Run: web            INGRESS ALL       |   <- only public surface
        |  Next.js App Router + BFF                    |
        +---------------------------------------------+
             |  OIDC, run.invoker           |  direct VPC egress
             v                              v
   +--------------------------+   +--------------------------+
   | Cloud Run: api           |   | Cloud SQL PostgreSQL     |
   | INTERNAL_ONLY            |-->| private IP, pgvector     |
   | triage / RAG / MCP       |   | IAM auth, no password    |
   +--------------------------+   +--------------------------+
             ^                              ^
             |                              |
   +--------------------------+             |
   | Cloud Run: worker        |-------------+
   | INTERNAL_ONLY            |
   +--------------------------+
        ^              ^
        | OIDC push    | OIDC
   +---------+    +-----------+
   | Pub/Sub |    | Scheduler |
   +---------+    +-----------+
        ^
        | publish (gmail-api-push@system.gserviceaccount.com)
   Gmail watch notifications
```

Every outbound call - model provider, Gmail, Google APIs - leaves through the
environment VPC and Cloud NAT, so egress has a stable, auditable path.

## Security decisions this phase is accountable for

**No public database.** Cloud SQL has `ipv4_enabled = false` and no
`authorized_networks`. It is reachable only over private service access from
the environment VPC, and requires encrypted connections.

**No database password exists.** Services authenticate as
`CLOUD_IAM_SERVICE_ACCOUNT` users. There is no password to store, rotate, leak
into state, or discover in a log.

**No secret value is in Terraform.** The `secrets` module creates empty
containers and access grants. It deliberately creates no
`google_secret_manager_secret_version`, and uses no `random_password`. Values
are injected out of band by an authorized client operator (see
[RUNBOOK.md](RUNBOOK.md)). This is asserted by an automated check, so a future
change that adds a secret value to the repository fails the build.

**One secret is writable by the application, and only add-only.** The client's
own provider key (`llm-provider-api-key`) is the one value an Owner replaces
from the workspace, so the `api` service account holds
`secretmanager.secretVersionAdder` on that secret alone: it may add a version,
never read, disable, or destroy one. Every other value stays out of band, and a
check fails the build if a secret that is not labelled `client-byok` is made
writable by a runtime service, or if any identity is granted a broad Secret
Manager role.

**One public surface.** Only the web tier may accept non-internal ingress. The
`runtime` module rejects a configuration where a service marked `private_tier`
is reachable from outside the VPC, and refuses more than one public service.

**No service account keys.** CI federates through Workload Identity Federation.
The provider carries an attribute condition pinning it to one GitHub
repository, and each CI identity is further narrowed - production to a
protected GitHub environment that requires manual approval.

**Separated CI privilege.** The `deploy` identity pushes images and rolls
revisions; it holds no secret access. The `infra` identity runs Terraform and is
deliberately broad, which is why it is constrained by a protected environment,
required review, and an access-audited state bucket.

**CMEK per data class.** Separate keys for database, storage, Pub/Sub, secrets,
and images, so one can be rotated or revoked without touching the others. Keys
carry `prevent_destroy`: a destroyed key takes its ciphertext with it.

**Deploys are not owned by Terraform.** Cloud Run services ignore image drift,
so `terraform apply` can never silently roll a running service back to an older
revision.

**Migrations run as a job, not on boot.** A service that migrates on start-up
races itself across revisions.

**Sending stays off.** Every service receives `RESOLVEFLOW_SENDING_ENABLED=false`.
C02 creates no path that weakens the C12 prohibition, and no queue, topic, job,
or IAM grant in this definition can send a customer message.

## Verification

C02 is a definition phase, so verification is offline:

```bash
make infra-check                       # terraform fmt + validate when available
python -m scripts.validate_infra       # structural and posture checks, always
python -m pytest tests/test_infra_definition.py
```

`scripts/validate_infra.py` parses the HCL and checks module wiring (unknown
arguments, missing required arguments, references to outputs that do not exist)
plus the posture decisions above. Each check has a paired negative test that
breaks a copy of the definition and requires the check to fail, so the suite
cannot pass vacuously.

## Required client inputs

`terraform plan` fails without these rather than defaulting them. Each is a gate
recorded in `CLIENT_SCOPE.md`.

| Input | Used by | Blocking |
|---|---|---|
| Staging and production project IDs | Both roots | Any apply |
| Approved region | Both roots | Any apply; V1 is single-region |
| GitHub repository (`owner/name`) | WIF attribute condition | Any apply |
| Billing account | Budgets | Budget creation only; omit to skip |
| Operations contact emails | Alerts and budgets | Meaningful alerting |
| Staging tester group | `web_invoker_members` | Reaching staging at all |
| State bucket name | `bootstrap`, `backend.hcl` | Any apply |
| Retention periods | Bucket lifecycle rules | Production apply (C13) |
| Launch approval | Production `web_invoker_members` | Public reachability (C15) |

## Deliberately deferred

- **Domain mapping, TLS, Cloud Armor, and IAP.** No client domain exists yet.
  The uptime check stays disabled until a hostname is mapped. Edge protection is
  a C13 decision.
- **Binary Authorization.** Image signing requires a release process that C02
  does not yet have; revisit at C13.
- **Read replicas.** The proposed RPO of 24 hours and RTO of 4 hours are met by
  PITR plus regional availability. A replica is a cost decision, not a
  correctness one, and belongs with the client's business-impact review.
- **VPC Service Controls.** Worth evaluating at C13; it materially complicates
  the deploy path and should not be adopted before the pipeline is stable.
- **pgvector and all schema.** Owned by the Alembic chain (C04/C05) so schema
  history has one source of truth. Terraform provisions the instance and
  database only.
