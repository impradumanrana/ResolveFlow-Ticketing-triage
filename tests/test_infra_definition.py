"""C02 gate tests for the client-owned GCP infrastructure definition.

These run entirely offline. They never authenticate to Google, never read a
credential, and never create, read, or mutate a cloud resource.

Each posture check has a paired negative test: a copy of the definition is
deliberately broken in a temporary directory and the check must fail on it.
A validator that only ever passes proves nothing.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from scripts.validate_infra import (
    TERRAFORM_ROOT,
    check_balance,
    collect_findings,
    strip_noise,
    top_level_argument_names,
)

# The repository root, derived from this file rather than from the working
# directory, so the checks below read the same files however pytest is invoked.
ROOT = Path(__file__).resolve().parents[1]

ENVIRONMENTS = ("staging", "production")


@pytest.fixture(scope="module")
def findings() -> list:
    return collect_findings()


@pytest.fixture
def broken_root(tmp_path: Path) -> Path:
    """A writable copy of the definition, for negative tests."""
    destination = tmp_path / "terraform"
    shutil.copytree(TERRAFORM_ROOT, destination)
    return destination


def rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture text not found in {path.name}: {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def messages(results: list) -> str:
    return "\n".join(finding.render() for finding in results)


# --------------------------------------------------------------------------
# The definition as committed
# --------------------------------------------------------------------------


def test_definition_passes_all_offline_checks(findings: list) -> None:
    assert findings == [], messages(findings)


def test_both_environments_are_defined() -> None:
    for environment in ENVIRONMENTS:
        root = TERRAFORM_ROOT / "envs" / environment
        assert root.is_dir(), f"missing {environment} root module"
        assert (root / "main.tf").is_file()
        assert (root / "terraform.tfvars.example").is_file()


def test_environments_do_not_share_a_project_or_network() -> None:
    staging = (TERRAFORM_ROOT / "envs" / "staging" / "main.tf").read_text(encoding="utf-8")
    production = (TERRAFORM_ROOT / "envs" / "production" / "main.tf").read_text(encoding="utf-8")

    staging_cidrs = set(re.findall(r'"(10\.[\d.]+/?\d*)"', staging))
    production_cidrs = set(re.findall(r'"(10\.[\d.]+/?\d*)"', production))

    assert staging_cidrs, "staging declares no network ranges"
    assert production_cidrs, "production declares no network ranges"
    assert not staging_cidrs & production_cidrs


def test_no_client_identifier_is_committed() -> None:
    """Project ids, domains, and billing accounts are client inputs, not defaults."""
    for path in TERRAFORM_ROOT.rglob("*.tf"):
        code = strip_noise(path.read_text(encoding="utf-8"))
        assert "billingAccounts/" not in code, f"{path} hardcodes a billing account"


def test_tfvars_examples_only_contain_placeholders() -> None:
    """Client-identifying settings must be placeholders, never a working default.

    Neutral operational defaults such as a time zone or a budget figure are
    fine: they identify nobody and a reviewer can see them at a glance.
    """
    client_identifying = {
        "project_id",
        "region",
        "github_repository",
        "billing_account_id",
        "state_bucket_name",
        "impersonate_service_account",
        "uptime_check_host",
    }

    examples = list(TERRAFORM_ROOT.rglob("terraform.tfvars.example"))
    assert examples, "no tfvars examples found"

    for path in examples:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() not in client_identifying:
                continue
            assert "REPLACE" in value, f"{path}: real-looking value for {key.strip()}:{value}"


def test_provider_checksums_are_pinned_for_ci_and_developer_platforms() -> None:
    """The lock file is committed on purpose: it pins provider builds."""
    for name in ("bootstrap", "envs/staging", "envs/production"):
        lock = TERRAFORM_ROOT / name / ".terraform.lock.hcl"
        assert lock.is_file(), f"{name} has no provider lock file"
        text = lock.read_text(encoding="utf-8")
        assert 'provider "registry.terraform.io/hashicorp/google"' in text
        assert text.count("h1:") >= 2, f"{name} lock covers too few platforms"


def test_no_real_state_or_variable_files_are_committed() -> None:
    assert not list(TERRAFORM_ROOT.rglob("*.tfstate"))
    assert not list(TERRAFORM_ROOT.rglob("terraform.tfvars"))
    assert not list(TERRAFORM_ROOT.rglob("backend.hcl"))


def test_sending_stays_disabled_in_every_deployed_environment() -> None:
    """C02 must not create a path the C12 prohibition does not cover."""
    code = (TERRAFORM_ROOT / "modules" / "environment" / "main.tf").read_text(encoding="utf-8")
    assert re.search(r'RESOLVEFLOW_SENDING_ENABLED\s*=\s*"false"', code)


def test_the_mailbox_scope_profile_defaults_closed() -> None:
    """C-D125: drafting is a client decision, so the platform default is read-only."""
    variables = (TERRAFORM_ROOT / "modules" / "environment" / "variables.tf").read_text(
        encoding="utf-8"
    )
    main = (TERRAFORM_ROOT / "modules" / "environment" / "main.tf").read_text(encoding="utf-8")

    block = variables.split('variable "mailbox_scope_profile"')[1].split("\nvariable ")[0]
    assert re.search(r'default\s*=\s*"read_only"', block)
    # Only the two profiles, so a typo in a tfvars file fails the plan.
    assert re.search(
        r'contains\(\["read_only", "read_and_draft"\], var\.mailbox_scope_profile\)', block
    )
    assert "gmail.send" not in block

    assert re.search(r"RESOLVEFLOW_MAILBOX_SCOPES\s*=\s*var\.mailbox_scope_profile", main), (
        "the service does not receive the profile"
    )


def test_no_terraform_resource_creates_a_secret_value() -> None:
    for path in TERRAFORM_ROOT.rglob("*.tf"):
        code = strip_noise(path.read_text(encoding="utf-8"))
        assert "google_secret_manager_secret_version" not in code, path
        assert "random_password" not in code, path


# --------------------------------------------------------------------------
# Negative tests: each check must actually fire
# --------------------------------------------------------------------------


def test_detects_public_database(broken_root: Path) -> None:
    rewrite(
        broken_root / "modules" / "database" / "main.tf",
        "ipv4_enabled                                  = false",
        "ipv4_enabled                                  = true",
    )
    results = collect_findings(root=broken_root)
    assert any("public IP" in finding.message for finding in results), messages(results)


def test_detects_public_bucket(broken_root: Path) -> None:
    rewrite(
        broken_root / "modules" / "storage" / "main.tf",
        'public_access_prevention    = "enforced"',
        'public_access_prevention    = "inherited"',
    )
    results = collect_findings(root=broken_root)
    assert any("public access prevention" in finding.message for finding in results)


def test_detects_private_service_exposed_to_the_internet(broken_root: Path) -> None:
    anchor = '      service_account_key = "api"'
    rewrite(
        broken_root / "modules" / "environment" / "main.tf",
        anchor,
        '      ingress             = "INGRESS_TRAFFIC_ALL"\n' + anchor,
    )
    results = collect_findings(root=broken_root)
    assert any(
        "public ingress" in finding.message or "internal" in finding.message for finding in results
    ), messages(results)


def test_detects_a_committed_credential(broken_root: Path) -> None:
    target = broken_root / "envs" / "staging" / "terraform.tfvars.example"
    target.write_text(
        target.read_text(encoding="utf-8") + '\n# leaked = "sk-abcdefghijklmnopqrstuvwxyz0123"\n',
        encoding="utf-8",
    )
    results = collect_findings(root=broken_root)
    assert any("API key" in finding.message for finding in results), messages(results)


def test_detects_a_committed_tfvars_file(broken_root: Path) -> None:
    (broken_root / "envs" / "staging" / "terraform.tfvars").write_text(
        'project_id = "some-real-project"\n', encoding="utf-8"
    )
    results = collect_findings(root=broken_root)
    assert any("must not be committed" in finding.message for finding in results)


def test_detects_a_secret_value_created_in_terraform(broken_root: Path) -> None:
    target = broken_root / "modules" / "secrets" / "main.tf"
    target.write_text(
        target.read_text(encoding="utf-8")
        + '\nresource "google_secret_manager_secret_version" "bad" {\n  secret = "x"\n}\n',
        encoding="utf-8",
    )
    results = collect_findings(root=broken_root)
    assert any("out of band" in finding.message for finding in results)


def test_detects_a_missing_required_module_argument(broken_root: Path) -> None:
    target = broken_root / "modules" / "environment" / "main.tf"
    text = target.read_text(encoding="utf-8")
    # Drop the `environment` argument from the network module call.
    updated = text.replace("  environment                          = var.environment\n", "", 1)
    assert updated != text
    target.write_text(updated, encoding="utf-8")

    results = collect_findings(root=broken_root)
    assert any("omits required argument" in finding.message for finding in results), messages(
        results
    )


def test_detects_a_reference_to_an_output_that_does_not_exist(broken_root: Path) -> None:
    rewrite(
        broken_root / "modules" / "environment" / "main.tf",
        "module.network.subnet_id",
        "module.network.subnet_identifier",
    )
    results = collect_findings(root=broken_root)
    assert any("does not output" in finding.message for finding in results), messages(results)


def test_detects_an_undeclared_variable(broken_root: Path) -> None:
    rewrite(
        broken_root / "modules" / "kms" / "main.tf",
        "var.rotation_period",
        "var.rotation_period_undeclared",
    )
    results = collect_findings(root=broken_root)
    assert any("never declares it" in finding.message for finding in results), messages(results)


def test_detects_unbalanced_configuration(broken_root: Path) -> None:
    target = broken_root / "modules" / "kms" / "outputs.tf"
    target.write_text(
        target.read_text(encoding="utf-8") + '\noutput "broken" {\n', encoding="utf-8"
    )
    results = collect_findings(root=broken_root)
    assert any("unclosed" in finding.message for finding in results), messages(results)


def test_detects_a_removed_remote_backend(broken_root: Path) -> None:
    target = broken_root / "envs" / "staging" / "versions.tf"
    text = target.read_text(encoding="utf-8")
    target.write_text(re.sub(r'backend "gcs" \{[^}]*\}', "", text), encoding="utf-8")
    results = collect_findings(root=broken_root)
    assert any("remote backend" in finding.message for finding in results), messages(results)


# --------------------------------------------------------------------------
# Parser behaviour the checks depend on
# --------------------------------------------------------------------------


def test_strip_noise_blanks_comments_but_keeps_strings() -> None:
    source = 'a = "kept" # dropped\nb = "also kept"\n'
    cleaned = strip_noise(source)
    assert '"kept"' in cleaned
    assert "dropped" not in cleaned
    assert '"also kept"' in cleaned


def test_strip_noise_blanks_heredoc_bodies() -> None:
    source = 'description = <<-DESC\n  a { brace that would break parsing\nDESC\nname = "x"\n'
    cleaned = strip_noise(source)
    assert "brace that would break" not in cleaned
    assert '"x"' in cleaned
    assert check_balance(cleaned, Path("x.tf")) == []


def test_top_level_argument_names_sees_labelled_blocks() -> None:
    body = '\n  required_version = ">= 1.9"\n  backend "gcs" {\n    prefix = "p"\n  }\n'
    names = top_level_argument_names(body)
    assert names == {"required_version", "backend"}


def test_top_level_argument_names_ignores_nested_keys() -> None:
    body = "\n  outer = 1\n  block {\n    inner = 2\n  }\n"
    assert top_level_argument_names(body) == {"outer", "block"}


def test_only_the_client_byok_key_is_writable_by_a_runtime() -> None:
    environment = strip_noise(
        (TERRAFORM_ROOT / "modules" / "environment" / "main.tf").read_text(encoding="utf-8")
    )
    assert environment.count("version_adders") == 1
    block = environment[environment.index("llm-provider-api-key") :]
    block = block[: block.index("}")]
    assert 'secret_class = "client-byok"' in block
    assert 'version_adders = [module.service_accounts.members["api"]]' in block

    secrets = strip_noise(
        (TERRAFORM_ROOT / "modules" / "secrets" / "main.tf").read_text(encoding="utf-8")
    )
    assert '"roles/secretmanager.secretVersionAdder"' in secrets


def test_detects_a_runtime_writable_non_byok_secret(broken_root: Path) -> None:
    rewrite(
        broken_root / "modules" / "environment" / "main.tf",
        '      secret_class = "session-signing"\n',
        '      secret_class = "session-signing"\n'
        '      version_adders = [module.service_accounts.members["web"]]\n',
    )
    results = collect_findings(root=broken_root)
    assert any(
        "auth-session-secret is writable by a runtime" in finding.message for finding in results
    ), messages(results)


def test_detects_the_byok_secret_losing_its_class(broken_root: Path) -> None:
    # Proves the check really inspects the BYOK block rather than skipping it.
    rewrite(
        broken_root / "modules" / "environment" / "main.tf",
        'secret_class = "client-byok"',
        'secret_class = "service-to-service"',
    )
    results = collect_findings(root=broken_root)
    assert any(
        "llm-provider-api-key is writable by a runtime" in finding.message for finding in results
    ), messages(results)


def test_detects_a_broad_secret_manager_role(broken_root: Path) -> None:
    rewrite(
        broken_root / "modules" / "secrets" / "main.tf",
        'role      = "roles/secretmanager.secretVersionAdder"',
        'role      = "roles/secretmanager.admin"',
    )
    results = collect_findings(root=broken_root)
    assert any("secretmanager.admin" in finding.message for finding in results), messages(results)


# ---------------------------------------------------------------------------
# C13: the checks CI runs are the checks the Makefile defines
# ---------------------------------------------------------------------------


def test_ci_does_not_keep_its_own_copy_of_the_lint_paths() -> None:
    """The defect this prevents actually happened.

    CI carried an inline list of lint and type-check paths. The Makefile's list
    moved on through C09 to C12 and CI's did not, so the rules engine, the AI
    gateway, the triage pipeline and the review service were unchecked in CI
    for four phases while every local run passed.
    """
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "make python-check" in workflow, "CI must call the Makefile, not repeat it"
    for path in ("app/rules", "app/gateway", "app/triage", "app/review", "app/security"):
        assert f"ruff check {path}" not in workflow
        assert f"mypy {path}" not in workflow


def test_the_makefile_states_its_paths_once() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "LINT_PATHS :=" in makefile
    assert "TYPE_PATHS :=" in makefile
    # Used, not restated: a second literal list is how the first one drifts.
    assert makefile.count("ruff check $(LINT_PATHS)") >= 2
    assert makefile.count("mypy $(TYPE_PATHS)") >= 2
    assert "ruff check app/api app/security" not in makefile


def test_the_whole_test_suite_is_linted() -> None:
    """A per-file list leaves a new test file unlinted until someone adds it."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    lint_block = makefile.split("LINT_PATHS :=")[1].split("\n\n")[0]

    assert " tests" in lint_block.replace("\\\n", " ")
    assert "tests/test_" not in lint_block, "individual test files should not be listed"


def test_ci_scans_dependencies_and_secrets() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pip_audit" in workflow
    # Blocking on what is deployed; the dev chain is reported separately.
    assert "npm audit --omit=dev --audit-level=high" in workflow
    # The action itself, not the word: "gitleaks" also appears in a comment
    # above it, which let a mutant replace the step with `run: true`.
    assert "uses: gitleaks/gitleaks-action@" in workflow
    assert (ROOT / ".gitleaks.toml").exists()


def test_the_secret_scan_allowlist_names_files_rather_than_patterns() -> None:
    """An allowlisted *pattern* would hide a real secret anywhere it appeared."""
    config = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")

    allowlist = config.split("[allowlist]")[1]
    assert "paths = [" in allowlist
    # No value-shaped allowlist entries: those would exempt the secret itself.
    assert "regexes" not in allowlist
    assert "stopwords" not in allowlist
