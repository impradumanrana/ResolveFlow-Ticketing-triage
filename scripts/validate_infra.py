"""Offline structural and safety checks for the client Terraform definition.

C02 defines infrastructure; it does not apply it. These checks therefore run
with no cloud credentials, no network access, and no Terraform binary. They
catch the two classes of defect that would otherwise only surface during an
apply against a client project:

1. Wiring errors - a module argument that does not exist, a required variable
   that is never passed, a `module.x.y` output that was never declared.
2. Posture errors - a public database, a world-readable bucket, a secret value
   committed to the repository, a private service made reachable.

When the Terraform CLI is present, `--with-terraform` additionally runs
`terraform fmt -check` and `terraform validate`. Those are stronger checks, but
they must never be the only ones: CI and a developer laptop cannot be assumed
to have the binary, and neither may block the definition gate.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM_ROOT = REPO_ROOT / "infra" / "terraform"

# Meta-arguments are accepted by every block and are not module variables.
META_ARGUMENTS = frozenset(
    {"source", "version", "count", "for_each", "providers", "depends_on", "lifecycle"}
)

# Google-owned identities that are genuinely constant and cannot be templated.
ALLOWED_LITERAL_SERVICE_ACCOUNTS = frozenset(
    {"gmail-api-push@system.gserviceaccount.com"}
)

SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "embedded private key"),
    (r"\bsk-[A-Za-z0-9_-]{16,}", "OpenAI-style API key"),
    (r"\bAIza[0-9A-Za-z_-]{30,}", "Google API key"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bghp_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
)


@dataclass
class Finding:
    """One failed check, reported against a file where possible."""

    path: Path
    message: str

    def render(self) -> str:
        try:
            location = self.path.relative_to(REPO_ROOT)
        except ValueError:
            location = self.path
        return f"{location}: {self.message}"


@dataclass
class Block:
    """A parsed HCL block header plus the span of its body."""

    kind: str
    labels: list[str]
    body: str


@dataclass
class ModuleDir:
    """A directory of .tf files treated as one Terraform module."""

    path: Path
    variables: dict[str, bool] = field(default_factory=dict)  # name -> has default
    outputs: set[str] = field(default_factory=set)
    used_vars: set[str] = field(default_factory=set)
    module_calls: list[tuple[str, str, set[str]]] = field(default_factory=list)
    module_output_refs: set[tuple[str, str]] = field(default_factory=set)
    code: str = ""
    has_backend: bool = False
    has_required_version: bool = False
    pinned_providers: bool = False


def strip_noise(text: str) -> str:
    """Return HCL with comments and heredoc bodies blanked, strings preserved.

    Blanking rather than deleting keeps offsets stable, so a later brace scan
    and the original text stay aligned.
    """
    out: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        pair = text[index : index + 2]

        if pair in ("//", "#" + text[index + 1 : index + 2]) and char == "/":
            end = text.find("\n", index)
            end = length if end == -1 else end
            out.append(" " * (end - index))
            index = end
            continue

        if char == "#":
            end = text.find("\n", index)
            end = length if end == -1 else end
            out.append(" " * (end - index))
            index = end
            continue

        if pair == "/*":
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            out.append("".join(" " if c != "\n" else "\n" for c in text[index:end]))
            index = end
            continue

        heredoc = re.match(r"<<[-~]?([A-Za-z_][A-Za-z0-9_]*)\r?\n", text[index:])
        if heredoc:
            marker = heredoc.group(1)
            body_start = index + heredoc.end()
            terminator = re.compile(rf"^[ \t]*{re.escape(marker)}[ \t]*$", re.MULTILINE)
            match = terminator.search(text, body_start)
            end = match.end() if match else length
            out.append("".join(" " if c != "\n" else "\n" for c in text[index:end]))
            index = end
            continue

        if char == '"':
            out.append(char)
            index += 1
            while index < length:
                current = text[index]
                out.append(current)
                index += 1
                if current == "\\" and index < length:
                    out.append(text[index])
                    index += 1
                    continue
                if current == '"':
                    break
            continue

        out.append(char)
        index += 1

    return "".join(out)


def check_balance(code: str, path: Path) -> list[Finding]:
    """Verify brace, bracket, and parenthesis balance outside string literals."""
    findings: list[Finding] = []
    pairs = {"}": "{", "]": "[", ")": "("}
    stack: list[tuple[str, int]] = []
    in_string = False
    index = 0

    while index < len(code):
        char = code[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char in "{[(":
            stack.append((char, code.count("\n", 0, index) + 1))
        elif char in pairs:
            if not stack or stack[-1][0] != pairs[char]:
                line = code.count("\n", 0, index) + 1
                findings.append(Finding(path, f"unbalanced '{char}' at line {line}"))
                return findings
            stack.pop()
        index += 1

    if stack:
        opener, line = stack[-1]
        findings.append(Finding(path, f"unclosed '{opener}' opened at line {line}"))
    return findings


def find_blocks(code: str, kind: str) -> list[Block]:
    """Extract top-level blocks of one kind with their bodies."""
    blocks: list[Block] = []
    pattern = re.compile(rf'(?m)^{re.escape(kind)}((?:\s+"[^"]*")*)\s*\{{')

    for match in pattern.finditer(code):
        labels = re.findall(r'"([^"]*)"', match.group(1))
        depth = 1
        index = match.end()
        in_string = False
        while index < len(code) and depth > 0:
            char = code[index]
            if in_string:
                if char == "\\":
                    index += 2
                    continue
                if char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
        blocks.append(Block(kind=kind, labels=labels, body=code[match.end() : index - 1]))

    return blocks


def top_level_argument_names(body: str) -> set[str]:
    """Names assigned at the immediate top level of a block body."""
    names: set[str] = set()
    depth = 0
    in_string = False
    line_start = 0
    index = 0

    while index <= len(body):
        char = body[index] if index < len(body) else "\n"
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char in "{[(":
            if depth == 0:
                segment = body[line_start:index]
                # Matches both `foo {` and a labelled block such as `backend "gcs" {`.
                match = re.match(
                    r'\s*([A-Za-z_][A-Za-z0-9_-]*)\s*(?:"[^"]*"\s*)*$', segment
                )
                if match:
                    names.add(match.group(1))
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == "=" and depth == 0 and body[index : index + 2] != "==":
            segment = body[line_start:index]
            match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_-]*)\s*$", segment)
            if match:
                names.add(match.group(1))
        elif char == "\n" and depth == 0:
            line_start = index + 1
        index += 1

    return names


def load_module_dir(path: Path) -> ModuleDir:
    module = ModuleDir(path=path)
    parts: list[str] = []

    for tf_file in sorted(path.glob("*.tf")):
        parts.append(strip_noise(tf_file.read_text(encoding="utf-8")))

    module.code = "\n".join(parts)

    for block in find_blocks(module.code, "variable"):
        if block.labels:
            module.variables[block.labels[0]] = "default" in top_level_argument_names(block.body)

    for block in find_blocks(module.code, "output"):
        if block.labels:
            module.outputs.add(block.labels[0])

    module.used_vars = set(re.findall(r"\bvar\.([A-Za-z_][A-Za-z0-9_]*)", module.code))

    for block in find_blocks(module.code, "module"):
        arguments = top_level_argument_names(block.body)
        source_match = re.search(r'(?m)^\s*source\s*=\s*"([^"]+)"', block.body)
        if source_match and block.labels:
            module.module_calls.append(
                (block.labels[0], source_match.group(1), arguments - META_ARGUMENTS)
            )

    module.module_output_refs = set(
        re.findall(r"\bmodule\.([A-Za-z_][A-Za-z0-9_-]*)\.([A-Za-z_][A-Za-z0-9_]*)", module.code)
    )

    for block in find_blocks(module.code, "terraform"):
        arguments = top_level_argument_names(block.body)
        module.has_required_version = "required_version" in arguments
        if "backend" in arguments:
            module.has_backend = True
        module.pinned_providers = bool(re.search(r'version\s*=\s*"[~>=\d]', block.body))

    return module


def discover_module_dirs(root: Path) -> dict[Path, ModuleDir]:
    dirs: dict[Path, ModuleDir] = {}
    for tf_file in root.rglob("*.tf"):
        parent = tf_file.parent
        if parent not in dirs:
            dirs[parent] = load_module_dir(parent)
    return dirs


def check_wiring(dirs: dict[Path, ModuleDir]) -> list[Finding]:
    findings: list[Finding] = []

    for path, module in sorted(dirs.items()):
        undeclared = module.used_vars - set(module.variables)
        for name in sorted(undeclared):
            findings.append(Finding(path, f"uses var.{name} but never declares it"))

        unused = set(module.variables) - module.used_vars
        for name in sorted(unused):
            findings.append(Finding(path, f"declares variable {name} but never uses it"))

        call_sources = {name: source for name, source, _ in module.module_calls}

        for call_name, source, arguments in module.module_calls:
            target = (path / source).resolve()
            if target not in dirs:
                findings.append(
                    Finding(path, f"module {call_name} points at missing module {source}")
                )
                continue

            target_module = dirs[target]
            unknown = arguments - set(target_module.variables)
            for name in sorted(unknown):
                findings.append(
                    Finding(path, f"module {call_name} passes unknown argument {name}")
                )

            required = {
                name for name, has_default in target_module.variables.items() if not has_default
            }
            missing = required - arguments
            for name in sorted(missing):
                findings.append(
                    Finding(path, f"module {call_name} omits required argument {name}")
                )

        for call_name, output_name in sorted(module.module_output_refs):
            call_source = call_sources.get(call_name)
            if call_source is None:
                findings.append(
                    Finding(path, f"references module.{call_name} which is not declared here")
                )
                continue
            target = (path / call_source).resolve()
            if target in dirs and output_name not in dirs[target].outputs:
                findings.append(
                    Finding(
                        path,
                        f"references module.{call_name}.{output_name}, "
                        f"which {call_source} does not output",
                    )
                )

    return findings


def check_versioning(dirs: dict[Path, ModuleDir]) -> list[Finding]:
    findings: list[Finding] = []
    for path, module in sorted(dirs.items()):
        if not module.has_required_version:
            findings.append(Finding(path, "no terraform required_version constraint"))
        if not module.pinned_providers:
            findings.append(Finding(path, "provider version is not pinned"))
    return findings


def check_environment_roots(dirs: dict[Path, ModuleDir], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    envs_root = root / "envs"
    if not envs_root.is_dir():
        return [Finding(root, "no envs directory; staging and production are undefined")]
    expected = {"staging", "production"}
    present = {path.name for path in envs_root.iterdir() if path.is_dir()}

    for name in sorted(expected - present):
        findings.append(Finding(envs_root, f"missing environment root module {name}"))

    for name in sorted(expected & present):
        path = envs_root / name
        module = dirs.get(path)
        if module is None:
            findings.append(Finding(path, "environment root contains no .tf files"))
            continue
        if not module.has_backend:
            findings.append(Finding(path, "environment root declares no remote backend"))
        if not module.module_calls:
            findings.append(Finding(path, "environment root calls no module"))

    # Separate projects are the whole point of separate environments.
    staging = dirs.get(envs_root / "staging")
    production = dirs.get(envs_root / "production")
    if staging and production:
        staging_cidrs = set(re.findall(r'"(10\.\d+\.\d+\.\d+/\d+)"', staging.code))
        production_cidrs = set(re.findall(r'"(10\.\d+\.\d+\.\d+/\d+)"', production.code))
        overlap = staging_cidrs & production_cidrs
        if overlap:
            findings.append(
                Finding(envs_root, f"staging and production share subnet ranges: {sorted(overlap)}")
            )

    return findings


def check_posture(dirs: dict[Path, ModuleDir], root: Path) -> list[Finding]:
    """Assert the security decisions this phase is accountable for."""
    findings: list[Finding] = []

    database = dirs.get(root / "modules" / "database")
    if database is None:
        findings.append(Finding(root, "database module is missing"))
    else:
        if not re.search(r"ipv4_enabled\s*=\s*false", database.code):
            findings.append(Finding(database.path, "Cloud SQL does not disable the public IP"))
        if "authorized_networks" in database.code:
            findings.append(
                Finding(
                    database.path,
                    "Cloud SQL declares authorized_networks; use private IP only",
                )
            )
        if not re.search(r"point_in_time_recovery_enabled\s*=\s*true", database.code):
            findings.append(
                Finding(database.path, "Cloud SQL does not enable point-in-time recovery")
            )
        if not re.search(r"ssl_mode\s*=\s*\"ENCRYPTED_ONLY\"", database.code):
            findings.append(
                Finding(database.path, "Cloud SQL does not require encrypted connections")
            )
        environment_code = dirs[root / "modules" / "environment"].code
        if not re.search(r"cloudsql\.iam_authentication", environment_code):
            findings.append(
                Finding(
                    database.path,
                    "Cloud SQL IAM authentication is not enabled by the environment",
                )
            )

    storage = dirs.get(root / "modules" / "storage")
    if storage is None:
        findings.append(Finding(root, "storage module is missing"))
    else:
        if not re.search(r"uniform_bucket_level_access\s*=\s*true", storage.code):
            findings.append(
                Finding(storage.path, "buckets do not enforce uniform bucket-level access")
            )
        if not re.search(r'public_access_prevention\s*=\s*"enforced"', storage.code):
            findings.append(
                Finding(storage.path, "buckets do not enforce public access prevention")
            )
        if re.search(r"force_destroy\s*=\s*true", storage.code):
            findings.append(Finding(storage.path, "a bucket sets force_destroy = true"))

    runtime = dirs.get(root / "modules" / "runtime")
    if runtime is not None and "ignore_changes" not in runtime.code:
        findings.append(
            Finding(
                runtime.path,
                "Cloud Run does not ignore image drift; apply could roll back a deploy",
            )
        )

    environment = dirs.get(root / "modules" / "environment")
    if environment is None:
        findings.append(Finding(root, "environment module is missing"))
    else:
        public_services = re.findall(r'ingress\s*=\s*"INGRESS_TRAFFIC_ALL"', environment.code)
        if len(public_services) > 1:
            findings.append(
                Finding(
                    environment.path,
                    f"{len(public_services)} services accept public ingress; only the web tier may",
                )
            )
        if not re.search(r'RESOLVEFLOW_SENDING_ENABLED\s*=\s*"false"', environment.code):
            findings.append(
                Finding(environment.path, "deployed environment does not pin sending to disabled")
            )
        for private in ("api", "worker"):
            block = re.search(rf"(?ms)^\s+{private}\s*=\s*\{{(.*?)^\s+\}}\n", environment.code)
            if block and "INGRESS_TRAFFIC_ALL" in block.group(1):
                findings.append(
                    Finding(environment.path, f"{private} service is not internal-only")
                )

    kms = dirs.get(root / "modules" / "kms")
    if kms is not None and "prevent_destroy" not in kms.code:
        findings.append(Finding(kms.path, "KMS keys are not protected from destruction"))

    secrets = dirs.get(root / "modules" / "secrets")
    if secrets is not None:
        if "google_secret_manager_secret_version" in secrets.code:
            findings.append(
                Finding(
                    secrets.path,
                    "a secret version is created in Terraform; "
                    "values must be added out of band",
                )
            )
        if "roles/secretmanager.secretAccessor" not in secrets.code:
            findings.append(Finding(secrets.path, "secret access grants are missing"))

    # A runtime may write only the client's own BYOK key. Every other secret
    # value is added out of band, and no identity may manage secrets broadly.
    if environment is not None:
        secret_blocks = re.finditer(
            r"(?ms)^\s{4}([a-z0-9-]+)\s*=\s*\{(.*?)^\s{4}\}", environment.code
        )
        for block in secret_blocks:
            name, body = block.group(1), block.group(2)
            if "version_adders" in body and 'secret_class = "client-byok"' not in body:
                findings.append(
                    Finding(environment.path, f"secret {name} is writable by a runtime service")
                )
    for directory in dirs.values():
        for role in ("roles/secretmanager.admin", "roles/secretmanager.secretVersionManager"):
            if role in directory.code:
                findings.append(Finding(directory.path, f"grants {role}; use per-secret add-only"))

    return findings


def check_committed_files(root: Path) -> list[Finding]:
    findings: list[Finding] = []

    for name in ("terraform.tfvars", "backend.hcl"):
        for path in root.rglob(name):
            findings.append(
                Finding(path, f"{name} must not be committed; commit only the .example")
            )

    for path in root.rglob("*.tfstate*"):
        findings.append(Finding(path, "Terraform state must never be committed"))

    # Each root module must pin provider checksums. Without the lock file a
    # later apply can silently pull a different provider build.
    root_modules = (
        root / "bootstrap",
        root / "envs" / "staging",
        root / "envs" / "production",
    )
    for root_module in root_modules:
        if not root_module.is_dir():
            continue
        lock = root_module / ".terraform.lock.hcl"
        if not lock.is_file():
            findings.append(
                Finding(root_module, "no .terraform.lock.hcl; provider checksums are unpinned")
            )
            continue
        text = lock.read_text(encoding="utf-8")
        if "linux_amd64" not in text and text.count("h1:") < 2:
            findings.append(
                Finding(
                    lock,
                    "lock file covers one platform; "
                    "CI and developer machines will disagree",
                )
            )

    return findings


def check_secrets_and_identifiers(root: Path) -> list[Finding]:
    """No credential, and no client identifier, may be committed."""
    findings: list[Finding] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".tf", ".hcl", ".example", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")

        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, text):
                findings.append(Finding(path, f"possible {label} committed"))

        if path.suffix != ".tf":
            continue

        code = strip_noise(text)
        # Descriptions legitimately name the values an operator must supply.
        code = re.sub(r'(?m)^\s*description\s*=\s*"(?:[^"\\]|\\.)*"', "", code)

        for member in re.findall(r'"(allUsers|allAuthenticatedUsers)"', code):
            findings.append(
                Finding(path, f'grants {member} directly; make it a variable the client must set')
            )

        for email in re.findall(r'"([^"$]*@[^"$]*\.gserviceaccount\.com)"', code):
            bare = email.split(":", 1)[-1]
            if bare not in ALLOWED_LITERAL_SERVICE_ACCOUNTS:
                findings.append(Finding(path, f"hardcoded service account {bare}"))

        for domain in re.findall(r'"[^"]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})"', code):
            if domain.endswith("gserviceaccount.com"):
                continue
            findings.append(Finding(path, f"hardcoded email domain {domain}"))

    return findings


def run_terraform_checks(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    binary = shutil.which("terraform")
    if binary is None:
        # Not a finding. The offline checks are the gate precisely because
        # neither CI nor a developer machine may be assumed to have the binary.
        print(
            "note: terraform not on PATH; skipped fmt and validate. "
            "CI runs both on every change.",
            file=sys.stderr,
        )
        return []

    fmt = subprocess.run(
        [binary, "fmt", "-check", "-recursive", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if fmt.returncode != 0:
        for line in fmt.stdout.splitlines():
            findings.append(Finding(Path(line.strip()), "not terraform fmt clean"))

    for env in ("staging", "production"):
        path = root / "envs" / env
        init = subprocess.run(
            [binary, "init", "-backend=false", "-input=false", "-no-color"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
        )
        if init.returncode != 0:
            findings.append(Finding(path, f"terraform init failed: {init.stderr.strip()[:400]}"))
            continue
        validate = subprocess.run(
            [binary, "validate", "-no-color"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
        )
        if validate.returncode != 0:
            findings.append(
                Finding(path, f"terraform validate failed: {validate.stdout.strip()[:800]}")
            )

    return findings


def collect_findings(with_terraform: bool = False, root: Path | None = None) -> list[Finding]:
    root = TERRAFORM_ROOT if root is None else root
    if not root.exists():
        return [Finding(root, "infrastructure definition directory is missing")]

    findings: list[Finding] = []

    for tf_file in sorted(root.rglob("*.tf")):
        findings.extend(check_balance(strip_noise(tf_file.read_text(encoding="utf-8")), tf_file))

    if findings:
        # Downstream parsing assumes balanced blocks.
        return findings

    dirs = discover_module_dirs(root)
    findings.extend(check_wiring(dirs))
    findings.extend(check_versioning(dirs))
    findings.extend(check_environment_roots(dirs, root))
    findings.extend(check_posture(dirs, root))
    findings.extend(check_committed_files(root))
    findings.extend(check_secrets_and_identifiers(root))

    if with_terraform:
        findings.extend(run_terraform_checks(root))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-terraform",
        action="store_true",
        help="Also run terraform fmt -check and terraform validate when the binary exists.",
    )
    args = parser.parse_args()

    findings = collect_findings(with_terraform=args.with_terraform)

    if findings:
        print(f"Infrastructure checks found {len(findings)} issue(s):", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding.render()}", file=sys.stderr)
        return 1

    module_count = len(list((TERRAFORM_ROOT / "modules").iterdir()))
    file_count = len(list(TERRAFORM_ROOT.rglob("*.tf")))
    print(f"Infrastructure checks passed: {file_count} files across {module_count} modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
