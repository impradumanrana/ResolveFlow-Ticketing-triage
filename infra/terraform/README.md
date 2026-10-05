# ResolveFlow Terraform

Infrastructure as code for the dedicated client deployment (plan phase C02).

**This has not been applied.** No cloud resource exists from this phase.
Applying it requires resolved client inputs and explicit authorization.

- Architecture, decisions, and required inputs: [../docs/INFRASTRUCTURE.md](../docs/INFRASTRUCTURE.md)
- Apply, secrets, deploy, rollback, restore: [../docs/RUNBOOK.md](../docs/RUNBOOK.md)

## Verify without a cloud account

```bash
make infra-check                              # fmt + validate when terraform exists
python -m scripts.validate_infra              # structural and posture checks
python -m pytest tests/test_infra_definition.py
```

## Committed and not committed

Committed: every `.tf` file, `.terraform.lock.hcl` (provider checksums), and
`*.example` templates.

Never committed: `terraform.tfvars`, `backend.hcl`, `*.tfstate`, `.terraform/`.
These hold client identifiers and environment shape. `.gitignore` covers them
and `scripts/validate_infra.py` fails the build if one appears.
