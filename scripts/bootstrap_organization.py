"""Seed the single organization and its Owner.

The client deployment initializes exactly one organization (decision C-D004).
This is the only path that creates it, and it is run once, deliberately, by an
authorized operator against a client environment.

Design notes:

* Idempotent and refusing rather than overwriting. If an organization already
  exists the script stops, because "bootstrap ran twice" and "someone is
  re-pointing this deployment at a different company" look identical from here.
* Planning is separated from execution. `build_plan` is pure and fully tested
  offline; only `apply_plan` touches a database, and it needs an explicit
  DATABASE_URL that no test supplies.
* The Owner is seeded, never emailed an invitation. An invitation flow that can
  mint an Owner is a privilege-escalation path (the migration also forbids it).
* No client value is hardcoded. Every identifier comes from arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
DOMAIN_PATTERN = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_DEPARTMENTS: tuple[tuple[str, str], ...] = (
    ("support", "Support"),
    ("billing", "Billing"),
    ("technical", "Technical"),
    ("security", "Security"),
)


class BootstrapError(Exception):
    """A refusal to bootstrap. Always fatal; never retried automatically."""


@dataclass(frozen=True)
class BootstrapPlan:
    """Exactly what will be written, decided before anything is written."""

    organization_slug: str
    organization_name: str
    domains: tuple[str, ...]
    owner_email: str
    departments: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...] = field(default=())

    def describe(self) -> str:
        lines = [
            "Bootstrap plan",
            f"  organization : {self.organization_name} ({self.organization_slug})",
            f"  domains      : {', '.join(self.domains)}",
            f"  owner        : {self.owner_email}",
            f"  departments  : {', '.join(slug for slug, _ in self.departments)}",
            "  sending      : disabled (Observe Mode)",
        ]
        lines.extend(f"  warning      : {warning}" for warning in self.warnings)
        return "\n".join(lines)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def build_plan(
    *,
    organization_slug: str,
    organization_name: str,
    domains: list[str],
    owner_email: str,
    departments: list[str] | None = None,
) -> BootstrapPlan:
    """Validate inputs and produce the plan. Pure: no database, no environment."""

    slug = organization_slug.strip().lower()
    if not SLUG_PATTERN.fullmatch(slug):
        raise BootstrapError(
            f"Organization slug {organization_slug!r} must be 3-63 lowercase "
            "letters, digits, or hyphens, and must not start or end with a hyphen."
        )

    name = organization_name.strip()
    if not name:
        raise BootstrapError("Organization name must not be blank.")

    normalized_domains = tuple(
        dict.fromkeys(domain.strip().lower() for domain in domains if domain.strip())
    )
    if not normalized_domains:
        raise BootstrapError(
            "At least one approved domain is required. Without it nobody can sign in."
        )
    for domain in normalized_domains:
        if not DOMAIN_PATTERN.fullmatch(domain):
            raise BootstrapError(f"Domain {domain!r} is not a valid domain name.")

    email = normalize_email(owner_email)
    if not EMAIL_PATTERN.fullmatch(email):
        raise BootstrapError(f"Owner email {owner_email!r} is not a valid address.")

    owner_domain = email.rsplit("@", 1)[1]
    if owner_domain not in normalized_domains:
        raise BootstrapError(
            f"Owner domain {owner_domain!r} is not in the approved domain list "
            f"{list(normalized_domains)}. The Owner would be unable to sign in."
        )

    if departments is None:
        chosen = DEFAULT_DEPARTMENTS
        warnings: tuple[str, ...] = (
            "Using placeholder departments. Confirm the client's real "
            "departments before go-live (CLIENT_SCOPE.md).",
        )
    else:
        seen: dict[str, str] = {}
        for entry in departments:
            dept_slug = entry.strip().lower()
            if not SLUG_PATTERN.fullmatch(dept_slug):
                raise BootstrapError(f"Department slug {entry!r} is not valid.")
            seen[dept_slug] = dept_slug.replace("-", " ").title()
        if not seen:
            raise BootstrapError("Department list was supplied but is empty.")
        chosen = tuple(seen.items())
        warnings = ()

    return BootstrapPlan(
        organization_slug=slug,
        organization_name=name,
        domains=normalized_domains,
        owner_email=email,
        departments=chosen,
        warnings=warnings,
    )


def apply_plan(connection: Any, plan: BootstrapPlan) -> str:
    """Execute the plan in one transaction. Refuses if an organization exists."""

    from sqlalchemy import text

    existing = connection.execute(
        text("SELECT slug FROM organizations ORDER BY created_at LIMIT 1")
    ).first()
    if existing is not None:
        raise BootstrapError(
            f"This deployment is already bootstrapped as {existing[0]!r}. "
            "Refusing to create a second organization. If this is wrong, the "
            "database - not this script - is the thing to investigate."
        )

    organization_id = connection.execute(
        text(
            "INSERT INTO organizations (slug, name, sending_enabled) "
            "VALUES (:slug, :name, false) RETURNING id"
        ),
        {"slug": plan.organization_slug, "name": plan.organization_name},
    ).scalar_one()

    for domain in plan.domains:
        connection.execute(
            text(
                "INSERT INTO organization_domains (organization_id, domain) "
                "VALUES (:organization_id, :domain)"
            ),
            {"organization_id": organization_id, "domain": domain},
        )

    for slug, name in plan.departments:
        connection.execute(
            text(
                "INSERT INTO departments (organization_id, slug, name) "
                "VALUES (:organization_id, :slug, :name)"
            ),
            {"organization_id": organization_id, "slug": slug, "name": name},
        )

    user_id = connection.execute(
        text(
            "INSERT INTO users (email) VALUES (:email) "
            "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id"
        ),
        {"email": plan.owner_email},
    ).scalar_one()

    # ACTIVE, not INVITED: the Owner must be able to sign in without anyone
    # having the authority to invite them yet.
    membership_id = connection.execute(
        text(
            "INSERT INTO memberships (organization_id, user_id, role, status, activated_at) "
            "VALUES (:organization_id, :user_id, 'OWNER', 'ACTIVE', now()) RETURNING id"
        ),
        {"organization_id": organization_id, "user_id": user_id},
    ).scalar_one()

    connection.execute(
        text(
            "INSERT INTO audit_events "
            "(organization_id, actor_user_id, actor_membership_id, actor_email, "
            " action, outcome, target_type, target_id, metadata) "
            "VALUES (:organization_id, :user_id, :membership_id, :email, "
            " 'organization.bootstrapped', 'ALLOWED', 'organization', :target, "
            # CAST(... AS jsonb) rather than `::jsonb`: SQLAlchemy's text()
            # parser treats the colons after a bind parameter as part of the
            # parameter name, so the value is never bound.
            " CAST(:metadata AS jsonb))"
        ),
        {
            "organization_id": organization_id,
            "user_id": user_id,
            "membership_id": membership_id,
            "email": plan.owner_email,
            "target": str(organization_id),
            "metadata": json.dumps(
                {
                    "departments": len(plan.departments),
                    "domains": len(plan.domains),
                }
            ),
        },
    )

    return str(organization_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True, help="Organization slug, e.g. acme.")
    parser.add_argument("--name", required=True, help="Organization display name.")
    parser.add_argument(
        "--domain",
        action="append",
        required=True,
        dest="domains",
        help="Approved Workspace domain. Repeat for more than one.",
    )
    parser.add_argument("--owner-email", required=True, help="Bootstrap Owner address.")
    parser.add_argument(
        "--department",
        action="append",
        dest="departments",
        help="Department slug. Repeat. Omit to use placeholders.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to the database. Without it the plan is printed and nothing is written.",
    )
    args = parser.parse_args(argv)

    try:
        plan = build_plan(
            organization_slug=args.slug,
            organization_name=args.name,
            domains=args.domains,
            owner_email=args.owner_email,
            departments=args.departments,
        )
    except BootstrapError as error:
        print(f"Refusing to bootstrap: {error}", file=sys.stderr)
        return 2

    print(plan.describe())

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print(
            "DATABASE_URL is required to apply the plan.",
            file=sys.stderr,
        )
        return 2

    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            organization_id = apply_plan(connection, plan)
    except BootstrapError as error:
        print(f"Refusing to bootstrap: {error}", file=sys.stderr)
        return 2

    print(f"\nBootstrapped organization {organization_id}.")
    print(f"{plan.owner_email} can now sign in as Owner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
