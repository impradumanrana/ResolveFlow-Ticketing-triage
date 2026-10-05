"""The production smoke test and the access review (C15).

Both run against a deployed environment, and both are run by an operator on a
day when something may already be wrong. So the properties that matter are
that they cannot change anything, that their verdicts can actually fail, and
that a check cannot pass by looking in the wrong place.

Their behaviour against a live stack is recorded in
`CLIENT_C15_TEST_REPORT.md`. These are the parts that hold offline.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from scripts.access_review import DORMANT_DAYS, EXPECTED_OWNERS, QUERIES, review
from scripts.smoke_test import (
    CHECKS_SQL,
    EXPECTED_HEAD,
    Report,
    check_api,
    check_web,
)

ROOT = Path(__file__).resolve().parents[1]
ORG = "11111111-1111-1111-1111-111111111111"

MUTATING = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|COPY)\b", re.IGNORECASE
)


# ===========================================================================
# NEITHER TOOL CAN CHANGE ANYTHING
# ===========================================================================


@pytest.mark.parametrize(("name", "statement"), sorted(CHECKS_SQL.items()))
def test_no_smoke_check_mutates_anything(name: str, statement: str) -> None:
    """A smoke test that writes to production leaves rows nobody asked for,
    and on a bad day makes the state harder to read."""
    assert not MUTATING.search(statement), f"{name}: {statement}"
    assert statement.strip().upper().startswith("SELECT"), name


@pytest.mark.parametrize(("name", "statement"), sorted(QUERIES.items()))
def test_no_access_review_query_mutates_anything(name: str, statement: str) -> None:
    """A review that changes access is not a review."""
    assert not MUTATING.search(statement), f"{name}: {statement}"
    assert statement.strip().upper().startswith("SELECT"), name


def test_neither_script_calls_a_paid_provider() -> None:
    """Spending money on every deploy is not a smoke test."""
    for name in ("smoke_test.py", "access_review.py"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        for forbidden in ("OpenAI", "openai", "api_key", "complete(", "embed("):
            assert forbidden not in source, f"{name}: {forbidden}"


def test_the_smoke_test_only_makes_get_requests() -> None:
    source = (ROOT / "scripts" / "smoke_test.py").read_text(encoding="utf-8")

    assert source.count('method="GET"') >= 1
    for verb in ('method="POST"', 'method="PUT"', 'method="DELETE"', 'method="PATCH"'):
        assert verb not in source, verb


# ===========================================================================
# THE SMOKE TEST'S VERDICT CAN FAIL
# ===========================================================================


class FakeHttp:
    """Stands in for the two deployed tiers, with a scripted response set."""

    def __init__(self, responses: dict[str, tuple[int, dict[str, str], str]]):
        self.responses = responses
        self.requested: list[str] = []

    def __call__(
        self, url: str, headers: dict[str, str] | None = None, *, follow: bool = True
    ) -> tuple[int, dict[str, str], str]:
        self.requested.append(url)
        for pattern, response in self.responses.items():
            if url.endswith(pattern):
                return response
        return 404, {}, ""


GOOD_POLICY = (
    "default-src 'none'; script-src 'self' 'nonce-{nonce}' 'strict-dynamic'; "
    "style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def web_responses(nonce: str = "a" * 32, **overrides: Any) -> dict[str, Any]:
    responses = {
        "/signin": (
            200,
            {
                "Content-Security-Policy": GOOD_POLICY.format(nonce=nonce),
                "X-Content-Type-Options": "nosniff",
            },
            "<html>sign in</html>",
        ),
        "/workspace": (307, {"Location": "/signin?next=%2Fworkspace"}, ""),
    }
    responses.update(overrides)
    return responses


def failures_of(report: Report) -> set[str]:
    return {result.name for result in report.failures}


def test_a_healthy_api_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.smoke_test as module

    http = FakeHttp(
        {
            "/healthz": (200, {}, ""),
            "/openapi.json": (404, {}, ""),
            "/v1/capabilities": (
                200,
                {},
                '{"automatic_sending": false, "pilot_stage": "OBSERVE", "observe_mode": true}',
            ),
        }
    )

    def routed(url: str, headers: dict[str, str] | None = None, **kwargs: Any):
        # Unauthenticated and bad-token probes must be refused.
        if url.endswith("/v1/capabilities"):
            authorization = (headers or {}).get("Authorization", "")
            if authorization != "Bearer real-token":
                return 401, {}, ""
            if "X-ResolveFlow-Organization-Id" not in (headers or {}):
                return 400, {}, ""
        return http(url, headers, **kwargs)

    monkeypatch.setattr(module, "_get", routed)
    report = Report()
    check_api(report, "https://api.example", "real-token")

    assert failures_of(report) == set()
    assert len(report.results) >= 7


def test_an_api_that_serves_capabilities_without_a_credential_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check that matters most: an unauthenticated caller must be refused."""
    import scripts.smoke_test as module

    monkeypatch.setattr(
        module,
        "_get",
        FakeHttp(
            {
                "/healthz": (200, {}, ""),
                "/openapi.json": (404, {}, ""),
                "/v1/capabilities": (
                    200,
                    {},
                    '{"automatic_sending": false, "pilot_stage": "OBSERVE"}',
                ),
            }
        ),
    )
    report = Report()
    check_api(report, "https://api.example", "real-token")

    assert "api refuses an unauthenticated caller" in failures_of(report)
    assert "api refuses a wrong credential" in failures_of(report)


def test_an_api_reporting_sending_enabled_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.smoke_test as module

    def routed(url: str, headers: dict[str, str] | None = None, **kwargs: Any):
        if url.endswith("/v1/capabilities"):
            if (headers or {}).get("Authorization") != "Bearer real-token":
                return 401, {}, ""
            if "X-ResolveFlow-Organization-Id" not in (headers or {}):
                return 400, {}, ""
            return 200, {}, '{"automatic_sending": true, "pilot_stage": "OBSERVE"}'
        if url.endswith("/healthz"):
            return 200, {}, ""
        return 404, {}, ""

    monkeypatch.setattr(module, "_get", routed)
    report = Report()
    check_api(report, "https://api.example", "real-token")

    assert "sending is off" in failures_of(report)


def test_exposed_docs_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.smoke_test as module

    def routed(url: str, headers: dict[str, str] | None = None, **kwargs: Any):
        if url.endswith("/openapi.json"):
            return 200, {}, "{}"
        if url.endswith("/v1/capabilities"):
            if (headers or {}).get("Authorization") != "Bearer real-token":
                return 401, {}, ""
            if "X-ResolveFlow-Organization-Id" not in (headers or {}):
                return 400, {}, ""
            return 200, {}, '{"automatic_sending": false, "pilot_stage": "OBSERVE"}'
        return 200, {}, ""

    monkeypatch.setattr(module, "_get", routed)
    report = Report()
    check_api(report, "https://api.example", "real-token")

    assert "docs are not exposed" in failures_of(report)


# ===========================================================================
# THE WEB CHECKS
# ===========================================================================


def test_a_correctly_configured_web_tier_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.smoke_test as module

    nonces = iter(["a" * 32, "b" * 32, "c" * 32, "d" * 32])

    def routed(url: str, headers: dict[str, str] | None = None, **kwargs: Any):
        if url.endswith("/signin"):
            return (
                200,
                {
                    "Content-Security-Policy": GOOD_POLICY.format(nonce=next(nonces)),
                    "X-Content-Type-Options": "nosniff",
                    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
                },
                "<html>sign in</html>",
            )
        if url.endswith("/workspace"):
            return 307, {"Location": "/signin?next=%2Fworkspace"}, ""
        return 404, {}, ""

    monkeypatch.setattr(module, "_get", routed)
    report = Report()
    check_web(report, "https://app.example")

    assert failures_of(report) == set()


def test_a_repeated_nonce_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A policy whose nonce never changes is decorative."""
    import scripts.smoke_test as module

    monkeypatch.setattr(module, "_get", FakeHttp(web_responses()))
    report = Report()
    check_web(report, "http://app.example")

    assert "each response gets its own nonce" in failures_of(report)


def test_a_missing_policy_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.smoke_test as module

    monkeypatch.setattr(
        module,
        "_get",
        FakeHttp(web_responses(**{"/signin": (200, {}, "<html>sign in</html>")})),
    )
    report = Report()
    check_web(report, "http://app.example")

    assert "a content-security policy is sent" in failures_of(report)
    assert "content sniffing is refused" in failures_of(report)


def test_an_inline_script_allowance_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.smoke_test as module

    weak = "default-src 'none'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
    monkeypatch.setattr(
        module,
        "_get",
        FakeHttp(
            web_responses(**{"/signin": (200, {"Content-Security-Policy": weak}, "<html></html>")})
        ),
    )
    report = Report()
    check_web(report, "http://app.example")

    assert "the policy permits no inline script" in failures_of(report)


def test_missing_hsts_over_https_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.smoke_test as module

    monkeypatch.setattr(module, "_get", FakeHttp(web_responses()))
    report = Report()
    check_web(report, "https://app.example")

    assert "HSTS is sent" in failures_of(report)


def test_a_workspace_served_without_a_session_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defect this prevents: urllib follows redirects, so the first version
    of this check reported 200 for the sign-in page it had been sent to, which
    is indistinguishable from the workspace being served to a stranger."""
    import scripts.smoke_test as module

    monkeypatch.setattr(
        module,
        "_get",
        FakeHttp(
            web_responses(
                **{
                    "/workspace": (
                        200,
                        {},
                        '<div class="ws-pagehead">Unified inbox</div>',
                    )
                }
            )
        ),
    )
    report = Report()
    check_web(report, "http://app.example")

    assert "the workspace refuses an unauthenticated visitor" in failures_of(report)
    assert "no workspace content is served without a session" in failures_of(report)


# ===========================================================================
# THE SMOKE TEST'S OWN EXPECTATIONS
# ===========================================================================


def test_the_expected_revision_is_the_real_head() -> None:
    """An expectation that falls behind the chain passes a stale deployment."""
    head = (ROOT / "tests" / "test_migration_foundation.py").read_text(encoding="utf-8")
    declared = re.search(r'HEAD_REVISION = "([^"]+)"', head)

    assert declared, "could not find HEAD_REVISION"
    assert EXPECTED_HEAD == declared.group(1)


def test_the_report_separates_warnings_from_failures() -> None:
    report = Report()
    report.record("a", True, "")
    report.record("b", False, "", critical=False)

    assert report.passed, "a warning must not fail the run"
    assert [r.name for r in report.warnings] == ["b"]

    report.record("c", False, "")
    assert not report.passed


# ===========================================================================
# THE ACCESS REVIEW
# ===========================================================================


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    """Returns scripted rows per query, matched by the query's own text."""

    def __init__(self, rows: dict[str, list[tuple[Any, ...]]]):
        self.rows = rows

    def execute(self, statement: object, parameters: dict[str, object] | None = None):  # noqa: ANN202
        text = str(statement)
        for name, query in QUERIES.items():
            if query == text:
                return _Rows(self.rows.get(name, []))
        raise AssertionError(f"unexpected query: {text[:60]}")


def empty_rows() -> dict[str, list[tuple[Any, ...]]]:
    return {name: [] for name in QUERIES}


def test_a_workspace_with_no_owner_is_a_finding() -> None:
    rows = empty_rows()
    rows["domain_allowlist"] = [("acme.example",)]
    rows["expired_session_still_present"] = [(0,)]

    result = review(FakeConnection(rows), ORG)

    assert not result.clean
    assert any("no active Owner" in finding for finding in result.findings)


def test_no_allowlisted_domain_is_a_finding() -> None:
    from datetime import UTC, datetime

    rows = empty_rows()
    rows["members"] = [("m-1", "owner@acme.example", "OWNER", "ACTIVE", datetime.now(UTC), None)]
    rows["expired_session_still_present"] = [(0,)]

    result = review(FakeConnection(rows), ORG)

    assert any("domain" in finding for finding in result.findings)


def test_a_revoked_member_with_a_live_session_is_a_finding() -> None:
    from datetime import UTC, datetime

    rows = empty_rows()
    rows["members"] = [("m-1", "owner@acme.example", "OWNER", "ACTIVE", datetime.now(UTC), None)]
    rows["domain_allowlist"] = [("acme.example",)]
    rows["expired_session_still_present"] = [(0,)]
    rows["live_session_for_inactive_member"] = [("leaver@acme.example", "REVOKED")]

    result = review(FakeConnection(rows), ORG)

    assert not result.clean
    assert any("has a session that has not expired" in f for f in result.findings)


def test_a_permission_held_by_an_inactive_member_is_a_finding() -> None:
    from datetime import UTC, datetime

    rows = empty_rows()
    rows["members"] = [("m-1", "owner@acme.example", "OWNER", "ACTIVE", datetime.now(UTC), None)]
    rows["domain_allowlist"] = [("acme.example",)]
    rows["expired_session_still_present"] = [(0,)]
    rows["permission_for_inactive_member"] = [
        ("leaver@acme.example", "support@acme.example", "SUSPENDED")
    ]

    result = review(FakeConnection(rows), ORG)

    assert any("still holds a permission" in f for f in result.findings)


def test_an_account_with_no_membership_is_a_finding() -> None:
    from datetime import UTC, datetime

    rows = empty_rows()
    rows["members"] = [("m-1", "owner@acme.example", "OWNER", "ACTIVE", datetime.now(UTC), None)]
    rows["domain_allowlist"] = [("acme.example",)]
    rows["expired_session_still_present"] = [(0,)]
    rows["account_without_membership"] = [("orphan@acme.example",)]

    result = review(FakeConnection(rows), ORG)

    assert any("no membership" in f for f in result.findings)


def test_too_many_owners_is_an_observation_not_a_finding() -> None:
    """Reporting a judgement call as a failure trains an administrator to
    ignore the output, which is worse than not running it."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    rows = empty_rows()
    rows["members"] = [
        (f"m-{index}", f"owner{index}@acme.example", "OWNER", "ACTIVE", now, now)
        for index in range(EXPECTED_OWNERS + 2)
    ]
    rows["domain_allowlist"] = [("acme.example",)]
    rows["expired_session_still_present"] = [(0,)]

    result = review(FakeConnection(rows), ORG)

    assert result.clean, result.findings
    assert any("active Owners" in note for note in result.observations)


def test_a_dormant_account_is_an_observation() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    stale = now - timedelta(days=DORMANT_DAYS + 5)
    rows = empty_rows()
    rows["members"] = [
        ("m-1", "owner@acme.example", "OWNER", "ACTIVE", now, now),
        ("m-2", "quiet@acme.example", "AGENT", "ACTIVE", now, stale),
    ]
    rows["mailbox_permissions"] = [("quiet@acme.example", "support@acme.example", True, True)]
    rows["domain_allowlist"] = [("acme.example",)]
    rows["expired_session_still_present"] = [(0,)]

    result = review(FakeConnection(rows), ORG)

    assert result.clean
    assert any("has not signed in for over" in note for note in result.observations)


def test_an_agent_with_no_mailbox_permission_is_an_observation() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    rows = empty_rows()
    rows["members"] = [
        ("m-1", "owner@acme.example", "OWNER", "ACTIVE", now, now),
        ("m-2", "agent@acme.example", "AGENT", "ACTIVE", now, now),
    ]
    rows["domain_allowlist"] = [("acme.example",)]
    rows["expired_session_still_present"] = [(0,)]

    result = review(FakeConnection(rows), ORG)

    assert result.clean
    assert any("holds no mailbox permission" in note for note in result.observations)


def test_an_organization_wide_role_is_not_flagged_for_lacking_a_permission() -> None:
    """Owner, Admin and Auditor see the whole organization by role, so a
    mailbox permission would be redundant - flagging them would be noise."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    rows = empty_rows()
    rows["members"] = [
        (f"m-{index}", f"{role.lower()}@acme.example", role, "ACTIVE", now, now)
        for index, role in enumerate(("OWNER", "ADMIN", "AUDITOR", "KNOWLEDGE_MANAGER"))
    ]
    rows["domain_allowlist"] = [("acme.example",)]
    rows["expired_session_still_present"] = [(0,)]

    result = review(FakeConnection(rows), ORG)

    assert not any("holds no mailbox permission" in note for note in result.observations)


def test_a_clean_workspace_reports_nothing() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    rows = empty_rows()
    rows["members"] = [
        ("m-1", "owner@acme.example", "OWNER", "ACTIVE", now, now),
        ("m-2", "agent@acme.example", "AGENT", "ACTIVE", now, now),
    ]
    rows["mailbox_permissions"] = [("agent@acme.example", "support@acme.example", True, True)]
    rows["domain_allowlist"] = [("acme.example",)]
    rows["expired_session_still_present"] = [(0,)]

    result = review(FakeConnection(rows), ORG)

    assert result.clean
    assert result.observations == []
    assert result.data["summary"]["by_role"] == {"AGENT": 1, "OWNER": 1}
    assert result.data["summary"]["can_action_a_mailbox"] == 1


# ===========================================================================
# THE HANDOVER ARTIFACT
# ===========================================================================


def test_the_handover_document_exists_and_is_not_a_stub() -> None:
    handover = ROOT / "docs" / "HANDOVER.md"

    assert handover.exists()
    text = handover.read_text(encoding="utf-8")
    assert len(text) > 4000, "a handover checklist that fits on a page is not one"


def test_the_handover_covers_every_gate_item() -> None:
    """C15's gate is smoke tests, alerts, audit, backups, budgets, access
    review and handover. Each needs a row somebody initials."""
    text = (ROOT / "docs" / "HANDOVER.md").read_text(encoding="utf-8").casefold()

    for item in (
        "smoke test",
        "alert polic",
        "append-only",
        "backups enabled",
        "budget",
        "access review",
        "retention",
        "data-subject export",
    ):
        assert item in text, item


def test_the_handover_names_the_rollback_and_support_boundaries() -> None:
    """The plan asks for these in writing, and "we assumed you would handle
    that" is the most expensive sentence in a handover."""
    text = (ROOT / "docs" / "HANDOVER.md").read_text(encoding="utf-8")

    assert "## 4. Rollback and support boundaries" in text
    assert "no 24/7 rota" in text.casefold()
    assert "What ends when support ends" in text


def test_the_handover_states_the_limitations_rather_than_burying_them() -> None:
    text = (ROOT / "docs" / "HANDOVER.md").read_text(encoding="utf-8").casefold()

    for limitation in (
        "no attachment is read",
        "no attachment scanner",
        "prompt injection",
        "no penetration test",
        "cannot reach backups",
    ):
        assert limitation in text, limitation


def test_the_handover_signature_blocks_are_empty() -> None:
    """Pre-filled signatures would make an unsigned handover look signed."""
    text = (ROOT / "docs" / "HANDOVER.md").read_text(encoding="utf-8")

    block = text.split("## 6. Sign-off")[1]
    rows = [
        line
        for line in block.splitlines()
        if line.startswith("| Client") or line.startswith("| Delivery")
    ]
    assert rows, "no signature rows found"
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert all(not cell for cell in cells[1:]), f"pre-filled: {row}"
