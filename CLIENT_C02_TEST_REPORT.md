# ResolveFlow AI - C02 Verification Report

**Phase:** C02 - Client-owned GCP foundation

**Result:** GREEN for the definition gate. The apply half of the plan's C02 gate is **not** met and is blocked on client inputs and authorization.

**Date:** 15 September 2026

## Scope and safety

No cloud resource was created, read, or mutated. No Google account was authenticated. No Gmail mailbox was connected. No paid model was called. No production data was touched. `gcloud` is installed on the development machine and was deliberately not used against any project.

The Terraform CLI was not installed on this machine at the start of the phase. Version 1.9.8 was downloaded to a session scratchpad outside the repository and used only for `fmt`, `init -backend=false`, `validate`, and `providers lock`. None of those commands contacts a Google project; `init -backend=false` and `providers lock` reach only the public Terraform registry to fetch the provider schema and its checksums.

## Delivered

- Terraform definition for two fully separate client-owned environments under `infra/terraform`, composed from 13 single-purpose modules.
- A `bootstrap` root module for the Terraform state bucket, which is the only configuration that cannot use remote state.
- Coverage of every resource class the phase requires: IAM and service accounts, Artifact Registry, Cloud Run, Cloud SQL with pgvector-capable PostgreSQL, Cloud Storage, Pub/Sub with dead letters, Cloud Tasks, Cloud Scheduler, Secret Manager, Cloud KMS, VPC with private service access and Cloud NAT, logging, log-based metrics, alert policies, an uptime check, backups with point-in-time recovery, and billing budgets.
- Workload Identity Federation for CI with no service account keys, and separated deploy and Terraform privilege.
- `scripts/validate_infra.py`: an offline HCL parser and checker for module wiring and security posture.
- `tests/test_infra_definition.py`: 24 tests, including a negative test for each posture check.
- `infra/docs/INFRASTRUCTURE.md`, `infra/docs/RUNBOOK.md`, and `infra/terraform/README.md`.
- `make infra-check`, a new CI `infrastructure` job, and Terraform entries in `.gitignore`.

## Automated gate evidence

All commands below were run and passed.

| Check | Command | Result |
|---|---|---|
| Terraform formatting | `terraform fmt -check -recursive infra/terraform` | PASS - clean, exit 0 |
| Staging schema validation | `terraform -chdir=infra/terraform/envs/staging validate` | PASS - "Success! The configuration is valid." |
| Production schema validation | `terraform -chdir=infra/terraform/envs/production validate` | PASS |
| Bootstrap schema validation | `terraform -chdir=infra/terraform/bootstrap validate` | PASS |
| Provider resolution | `terraform init -backend=false` | PASS - hashicorp/google 6.50.0 under the `~> 6.14` constraint |
| Provider checksum pinning | `terraform providers lock` for linux_amd64, darwin_arm64, darwin_amd64 | PASS - 3 lock files committed |
| Offline infrastructure checks | `python -m scripts.validate_infra` | PASS - 63 files across 13 modules, 0 findings |
| Infrastructure tests | `pytest tests/test_infra_definition.py` | PASS - 24 passed |
| Full Python suite | `pytest -q` | PASS - 74 passed |
| Python lint | `ruff check` incl. the two new files | PASS |
| Python types | `mypy` incl. `scripts/validate_infra.py` | PASS - no issues |
| Migration chain | `alembic upgrade head --sql` | PASS - unchanged from C01 |
| Web checks | `npm test`, `lint`, `typecheck`, `build` | PASS - unchanged from C01 |

### Defects the verification actually caught

Recorded because they are the evidence that these checks work rather than decorate the build:

1. **A real HCL syntax error.** Unescaped quotes inside a `description` in `envs/production/variables.tf` produced "Missing newline after argument". Found by `terraform fmt`, not by review. Fixed by converting the description to a heredoc.
2. **A wrong resource argument.** The Artifact Registry cleanup rules used `cleanup_policy`; the provider expects `cleanup_policies`. Found by `terraform validate` against the real provider schema. This would have failed only at apply time against the client project.
3. **Three validator defects,** found on its first run against the definition: labelled nested blocks such as `backend "gcs" {` were not recognised, a `description` mentioning a value was misread as granting it, and an allowlist compared an IAM member string against a bare email. All fixed; the backend check now has a negative test.

### What the offline checker verifies

Structure: brace balance; every `var.X` is declared and every declared variable is used; every module `source` resolves; no module call passes an unknown argument or omits a required one; every `module.x.y` reference names a declared output; every module pins a provider version and a `required_version`; both environment roots declare a remote backend; staging and production subnet ranges do not overlap.

Posture: Cloud SQL disables the public IP, declares no authorized networks, enables point-in-time recovery, requires encrypted connections, and uses IAM authentication; buckets enforce uniform bucket-level access and public access prevention and never set `force_destroy`; at most one service accepts public ingress and neither `api` nor `worker` does; Cloud Run ignores image drift; KMS keys are protected from destruction; no `google_secret_manager_secret_version` or `random_password` exists; every root module has a multi-platform provider lock; no `terraform.tfvars`, `backend.hcl`, or state file is committed; no credential pattern, hardcoded service account, or client email domain appears in any file.

Eleven negative tests break a copy of the definition and require the matching check to fail, so the suite cannot pass vacuously.

## Gate assessment, stated plainly

The plan's C02 gate reads: *"Staging deploys with private backend/worker/database access and documented rollback. Production still requires explicit approval."*

| Gate element | Status |
|---|---|
| Infrastructure as code defined and reviewed | **Met** |
| Private backend, worker, and database access | **Defined and machine-verified; not demonstrated,** because nothing is deployed |
| Documented rollback | **Met** as documentation (`infra/docs/RUNBOOK.md`); not exercised |
| Staging deploys | **Not met.** Requires client project IDs, region, billing, a tester group, and explicit authorization |
| Production requires explicit approval | **Met** - enforced by a protected GitHub environment, no committed credentials, and `prevent_destroy` |

The phase instruction was to define infrastructure as code and stop, without applying it or creating cloud resources. That is what was done. The deployment half of the gate is a client-input and authorization gate, not an engineering gap.

## Residual risks

| Risk | Treatment |
|---|---|
| `validate` checks schemas, not semantics. A first apply can still fail on quota, org policy, API enablement ordering, or service-agent timing | The runbook names the expected first-apply friction; apply staging first and review the plan line by line |
| Google service agents do not exist until their API is enabled, so a first-run KMS grant may fail | Documented; a second apply resolves it. Could be hardened later with explicit `google_project_service_identity` resources |
| Machine sizes, budgets, and retention periods are engineering defaults, not client-agreed figures | Every one is a variable; all are listed as gates in `CLIENT_SCOPE.md` and must be confirmed before production apply |
| No domain, TLS, WAF, or IAP is defined | Deliberate: no client domain exists. Recorded as a C13 decision; the uptime check stays disabled until a hostname exists |
| The `infra` CI identity holds broad roles | Constrained by a protected GitHub environment with manual approval, required review, and an access-audited state bucket. A narrower custom role is worth deriving from the first real apply's audit log |
| Backward-compatible migrations are assumed by the rollback procedure but not enforced | Flagged in the runbook; enforcement belongs with the C04 migration policy |
| Nothing here is proven against real quota or real load | C07 load simulation and the C14 staging pilot |

## Preserved working-tree state

`ResolveFlow-AI-Presentation.pptx` remained deleted and untouched. The untracked planning documents, local `.env`, and `app/data` were not modified. `.terraform/` directories created by `init` were removed; only `.tf`, `.terraform.lock.hcl`, and `*.example` files were added.

## Gate conclusion

C02's definition work is complete and verified offline. C03 identity and RBAC work may begin, since it is application work that does not depend on an applied environment. Applying this infrastructure remains unauthorized until the client supplies the inputs listed in `infra/docs/INFRASTRUCTURE.md` and explicitly approves the step.
