"""Offline tests for the single-organization bootstrap.

`build_plan` is pure, so every refusal is testable without a database. The
executor is exercised against a recording fake, which proves the statement
sequence and the refusal-on-existing-organization guard without connecting to
anything.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.bootstrap_organization import (
    BootstrapError,
    apply_plan,
    build_plan,
)

VALID = {
    "organization_slug": "acme",
    "organization_name": "Acme Support",
    "domains": ["acme.example"],
    "owner_email": "owner@acme.example",
}


def test_valid_input_produces_a_plan() -> None:
    plan = build_plan(**VALID)

    assert plan.organization_slug == "acme"
    assert plan.domains == ("acme.example",)
    assert plan.owner_email == "owner@acme.example"
    assert len(plan.departments) == 4


def test_placeholder_departments_are_flagged_as_unconfirmed() -> None:
    plan = build_plan(**VALID)

    assert plan.warnings, "placeholder departments must warn"
    assert "Confirm the client's real" in plan.warnings[0]


def test_explicit_departments_do_not_warn() -> None:
    plan = build_plan(**VALID, departments=["support", "billing"])

    assert plan.warnings == ()
    assert [slug for slug, _ in plan.departments] == ["support", "billing"]


def test_input_is_normalised() -> None:
    plan = build_plan(
        organization_slug="  ACME  ",
        organization_name="  Acme Support  ",
        domains=["  ACME.example ", "acme.example"],
        owner_email="  Owner@ACME.example ",
    )

    assert plan.organization_slug == "acme"
    assert plan.organization_name == "Acme Support"
    assert plan.domains == ("acme.example",)  # deduplicated
    assert plan.owner_email == "owner@acme.example"


@pytest.mark.parametrize(
    "slug",
    ["", "a", "-acme", "acme-", "Acme_Corp", "acme corp", "a" * 64],
)
def test_invalid_slugs_are_refused(slug: str) -> None:
    with pytest.raises(BootstrapError, match="slug"):
        build_plan(**{**VALID, "organization_slug": slug})


def test_blank_organization_name_is_refused() -> None:
    with pytest.raises(BootstrapError, match="name"):
        build_plan(**{**VALID, "organization_name": "   "})


def test_at_least_one_domain_is_required() -> None:
    """An empty allowlist would lock everyone out, including the Owner."""
    for domains in ([], ["   "]):
        with pytest.raises(BootstrapError, match="approved domain"):
            build_plan(**{**VALID, "domains": domains})


@pytest.mark.parametrize(
    "domain", ["not a domain", "acme", "acme..example", "-acme.example", "acme.example-"]
)
def test_invalid_domains_are_refused(domain: str) -> None:
    with pytest.raises(BootstrapError, match="valid domain"):
        build_plan(**{**VALID, "domains": [domain]})


@pytest.mark.parametrize(
    "email", ["", "owner", "owner@", "@acme.example", "owner@acme", "own er@acme.example"]
)
def test_invalid_owner_emails_are_refused(email: str) -> None:
    with pytest.raises(BootstrapError, match="valid address"):
        build_plan(**{**VALID, "owner_email": email})


def test_owner_outside_the_approved_domains_is_refused() -> None:
    """Seeding an Owner who cannot sign in produces a locked-out deployment."""
    with pytest.raises(BootstrapError, match="not in the approved domain list"):
        build_plan(**{**VALID, "owner_email": "owner@other.example"})


def test_invalid_department_slug_is_refused() -> None:
    with pytest.raises(BootstrapError, match="Department slug"):
        build_plan(**VALID, departments=["Support Team"])


class FakeResult:
    def __init__(self, first: Any = None, scalar: Any = None) -> None:
        self._first = first
        self._scalar = scalar

    def first(self) -> Any:
        return self._first

    def scalar_one(self) -> Any:
        return self._scalar


class RecordingConnection:
    """Records statements instead of executing them."""

    def __init__(self, existing_slug: str | None = None) -> None:
        self.existing_slug = existing_slug
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any]] = []

    def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> FakeResult:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        self.parameters.append(parameters or {})

        if sql.startswith("SELECT slug FROM organizations"):
            return FakeResult(first=(self.existing_slug,) if self.existing_slug else None)
        return FakeResult(scalar=f"id-{len(self.statements)}")


def test_apply_refuses_when_an_organization_already_exists() -> None:
    """C-D004: exactly one organization. A second one is never created."""
    connection = RecordingConnection(existing_slug="already-here")

    with pytest.raises(BootstrapError, match="already bootstrapped"):
        apply_plan(connection, build_plan(**VALID))

    # Nothing beyond the existence check was attempted.
    assert len(connection.statements) == 1
    assert connection.statements[0].startswith("SELECT slug FROM organizations")


def test_apply_writes_the_expected_records() -> None:
    connection = RecordingConnection()

    organization_id = apply_plan(
        connection, build_plan(**VALID, departments=["support", "billing"])
    )

    assert organization_id
    joined = " | ".join(connection.statements)
    assert "INSERT INTO organizations" in joined
    assert "INSERT INTO organization_domains" in joined
    assert "INSERT INTO departments" in joined
    assert "INSERT INTO users" in joined
    assert "INSERT INTO memberships" in joined
    assert "INSERT INTO audit_events" in joined


def test_the_bootstrap_owner_is_active_not_invited() -> None:
    """The first Owner cannot be invited: nobody yet has authority to invite."""
    connection = RecordingConnection()
    apply_plan(connection, build_plan(**VALID))

    membership = next(
        sql for sql in connection.statements if sql.startswith("INSERT INTO memberships")
    )
    assert "'OWNER', 'ACTIVE'" in membership


def test_bootstrap_never_enables_sending() -> None:
    connection = RecordingConnection()
    apply_plan(connection, build_plan(**VALID))

    organization = next(
        sql for sql in connection.statements if sql.startswith("INSERT INTO organizations")
    )
    assert "sending_enabled" in organization
    assert "false" in organization


def test_bootstrap_records_an_audit_event() -> None:
    connection = RecordingConnection()
    apply_plan(connection, build_plan(**VALID))

    assert any("organization.bootstrapped" in sql for sql in connection.statements)


def test_no_bind_parameter_is_followed_by_a_postgres_cast() -> None:
    """`:param::type` is parsed by SQLAlchemy as part of the parameter name.

    The recording fake cannot catch this because it never binds anything, so
    the rule is asserted directly against the statement text. Use
    CAST(:param AS type) instead.
    """
    connection = RecordingConnection()
    apply_plan(connection, build_plan(**VALID))

    for sql in connection.statements:
        assert "::" not in sql, f"bind parameter followed by a cast in: {sql}"


def test_every_write_is_parameterised() -> None:
    """No client value is ever interpolated into SQL text."""
    connection = RecordingConnection()
    apply_plan(connection, build_plan(**VALID))

    for sql in connection.statements:
        assert "acme.example" not in sql
        assert "owner@acme.example" not in sql
