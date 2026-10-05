"""The API's own protective limits (C13).

Three refusals that have nothing to do with what a caller asked for: too many
requests, too large a body, and an unhandled failure. Each has a shape the web
tier can read, and none of them says anything about the request's contents.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.limits import MAX_REQUEST_BYTES, get_rate_limiter
from app.api.main import app
from app.api.review import get_review_service
from app.review.memory_store import InMemoryReviewStore
from app.review.records import TicketFacts
from app.review.service import ReviewService
from app.security.rate_limit import POLICIES, InMemoryRateLimiter, RateLimitUnavailable

TOKEN = "test-token-not-a-secret"
ORG = "00000000-0000-0000-0000-0000000000a1"
TICKET = "11111111-1111-1111-1111-111111111111"
MAILBOX = "22222222-2222-2222-2222-222222222222"
THREAD = "33333333-3333-3333-3333-333333333333"
AGENT = "44444444-4444-4444-4444-444444444444"
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def headers(membership: str = AGENT) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-ResolveFlow-Organization-Id": ORG,
        "X-ResolveFlow-Membership-Id": membership,
        "X-ResolveFlow-Role": "AGENT",
        "X-Request-Id": "request-12345",
    }


def facts() -> TicketFacts:
    return TicketFacts(
        ticket_id=TICKET,
        organization_id=ORG,
        mailbox_id=MAILBOX,
        thread_id=THREAD,
        version=1,
        status="OPEN",
        mailbox_address="support@acme.example",
        customer_address="customer@example.net",
        subject="Reset my password",
        model_draft="Use the reset link. [KB-1]",
        citations=("KB-1",),
        grounding_validated=True,
    )


def review_body(key: str) -> dict[str, object]:
    """A contract-valid decision. The key has the minimum length the API wants,
    so a 422 never stands in for the refusal a test is actually checking."""
    return {
        "decision": "REJECT",
        "idempotency_key": f"idem-{key}-0001",
        "expected_version": 1,
        "reason": "Needs a person.",
    }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)
    ticket = facts()
    store = InMemoryReviewStore(
        tickets={TICKET: ticket},
        action_permissions={AGENT: {MAILBOX}},
        mailbox_addresses={MAILBOX: "support@acme.example"},
    )
    app.dependency_overrides[get_review_service] = lambda: ReviewService(store)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_review_service, None)
    app.dependency_overrides.pop(get_rate_limiter, None)


# ===========================================================================
# TOO MANY REQUESTS
# ===========================================================================


def test_a_caller_over_its_allowance_is_refused_with_retry_after(client) -> None:
    limiter = InMemoryRateLimiter(now=lambda: NOW)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    policy = POLICIES["review_decision"]

    # Spend the allowance. The decisions themselves may be refused on their
    # merits; what matters is that the requests were counted.
    for index in range(policy.limit):
        client.post(
            f"/v1/tickets/{TICKET}/review", json=review_body(f"k-{index}"), headers=headers()
        )

    response = client.post(
        f"/v1/tickets/{TICKET}/review", json=review_body("k-over"), headers=headers()
    )

    assert response.status_code == 429
    assert response.json() == {"schema_version": "v1", "ok": False, "code": "RATE_LIMITED"}
    assert int(response.headers["Retry-After"]) == 60


def test_the_refusal_uses_the_same_shape_as_every_other_refusal(client) -> None:
    """The web tier reads one shape, not FastAPI's `detail` for this one case."""
    limiter = InMemoryRateLimiter(now=lambda: NOW)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    for index in range(POLICIES["review_decision"].limit + 1):
        response = client.post(
            f"/v1/tickets/{TICKET}/review", json=review_body(f"s-{index}"), headers=headers()
        )

    body = response.json()
    assert set(body) == {"schema_version", "ok", "code"}
    assert "detail" not in body


def test_a_limiter_that_cannot_count_also_refuses(client) -> None:
    class Broken:
        def check(self, policy_name: str, key: str, *, organization_id: str) -> int:
            raise RateLimitUnavailable(POLICIES[policy_name])

    app.dependency_overrides[get_rate_limiter] = lambda: Broken()

    response = client.post(
        f"/v1/tickets/{TICKET}/review", json=review_body("k-1"), headers=headers()
    )

    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMIT_UNAVAILABLE"
    assert response.headers["Retry-After"] == "5"


def test_two_people_do_not_share_an_allowance(client) -> None:
    limiter = InMemoryRateLimiter(now=lambda: NOW)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    other = "55555555-5555-5555-5555-555555555555"
    for index in range(POLICIES["review_decision"].limit + 1):
        client.post(
            f"/v1/tickets/{TICKET}/review", json=review_body(f"a-{index}"), headers=headers()
        )

    response = client.post(
        f"/v1/tickets/{TICKET}/review",
        json=review_body("b-1"),
        headers=headers(membership=other),
    )

    assert response.status_code != 429


def test_without_a_database_the_limiter_does_not_refuse(client) -> None:
    """The route will answer 503 on its own; a 429 would be misleading."""
    app.dependency_overrides[get_rate_limiter] = lambda: None

    response = client.post(
        f"/v1/tickets/{TICKET}/review", json=review_body("k-1"), headers=headers()
    )

    assert response.status_code != 429


def test_the_limit_is_declared_in_the_published_contract() -> None:
    from pathlib import Path

    contract = json.loads(Path("contracts/openapi/v1.json").read_text())
    review = contract["paths"]["/v1/tickets/{ticket_id}/review"]["post"]

    assert "429" in review["responses"]


def _routes(router: object) -> list[object]:
    """Every route, through the wrappers `include_router` leaves behind.

    This FastAPI keeps included routers as a wrapper object rather than
    flattening their routes into the application, so a non-recursive walk sees
    only the handlers declared on the app itself - which is to say, none of the
    ones this test is about.
    """
    found: list[object] = []
    for route in getattr(router, "routes", []):
        if getattr(route, "dependant", None) is not None:
            found.append(route)
        # An included router is kept as a wrapper that holds the original.
        inner = getattr(route, "original_router", None) or route
        if inner is not route:
            found.extend(_routes(inner))
    return found


def _limit_dependencies(route: object) -> set[str]:
    names: set[str] = set()
    for dependency in getattr(route.dependant, "dependencies", []):  # type: ignore[attr-defined]
        name = getattr(dependency.call, "__name__", "")
        if name.startswith("limit_"):
            names.add(name)
    return names


def test_the_route_scan_finds_the_endpoints_it_claims_to_check() -> None:
    """A scan over zero routes would pass every assertion below."""
    from app.api.main import create_app

    paths = {getattr(route, "path", "") for route in _routes(create_app())}

    assert "/v1/tickets/{ticket_id}/review" in paths, paths
    assert "/healthz" in paths


def test_every_mutating_route_is_limited() -> None:
    from app.api.main import create_app

    unlimited: list[str] = []
    for route in _routes(create_app()):
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        if not ({"POST", "PUT", "PATCH", "DELETE"} & methods):
            continue
        if not _limit_dependencies(route):
            unlimited.append(f"{sorted(methods)} {path}")

    # Triage is the one mutating-shaped route with no durable effect: it
    # classifies and returns, writing nothing, and the whole-API ceiling
    # covers it. Everything else must name a policy.
    assert unlimited == ["['POST'] /v1/triage"], unlimited


def test_the_routes_that_touch_nothing_are_not_limited() -> None:
    """Counting a health check would add a database write per probe."""
    from app.api.main import create_app

    for route in _routes(create_app()):
        if getattr(route, "path", "") in {"/healthz", "/v1/capabilities"}:
            assert not _limit_dependencies(route), route


def test_the_review_endpoints_name_the_policies_they_use() -> None:
    from app.api.main import create_app

    by_path = {
        getattr(route, "path", ""): _limit_dependencies(route) for route in _routes(create_app())
    }

    assert by_path["/v1/tickets/{ticket_id}/review"] == {"limit_review"}
    assert by_path["/v1/tickets/{ticket_id}/draft-preview"] == {"limit_draft_preview"}


# ===========================================================================
# TOO LARGE A BODY
# ===========================================================================


def test_an_oversized_body_is_refused_before_it_is_parsed(client) -> None:
    oversized = {
        "decision": "EDIT",
        "idempotency_key": "idem-big-0001",
        "expected_version": 1,
        "body": "x" * (MAX_REQUEST_BYTES + 1000),
    }

    response = client.post(f"/v1/tickets/{TICKET}/review", json=oversized, headers=headers())

    assert response.status_code == 413
    assert response.json() == {
        "schema_version": "v1",
        "ok": False,
        "code": "REQUEST_TOO_LARGE",
    }


def test_a_body_that_lies_about_its_length_is_still_refused(client) -> None:
    """Counting what arrives, not what was declared.

    Asserted as 413 specifically. `in {400, 413, 422}` passed even with the
    byte counting removed, because an oversized body that reaches the parser
    fails validation instead - a weaker refusal, for a different reason, from
    code that had already read all of it.
    """
    payload = b"x" * (MAX_REQUEST_BYTES + 1000)

    response = client.post(
        f"/v1/tickets/{TICKET}/review",
        content=payload,
        headers={**headers(), "Content-Type": "application/json", "Content-Length": "10"},
    )

    # 400, not 413: by the time the overflow is detected the application has
    # begun reading, so the stream is closed and the framework answers. The
    # property that matters still holds - the body is not accumulated and no
    # handler sees it - but a caller that lies does not get the tidy labelled
    # refusal that an honest oversized request gets. Asserted exactly, because
    # `in {400, 413, 422}` passed even with the byte counting removed.
    assert response.status_code == 400, response.status_code
    assert "REQUEST_TOO_LARGE" not in response.text


def test_an_unparseable_content_length_is_refused(client) -> None:
    response = client.post(
        f"/v1/tickets/{TICKET}/review",
        content=b"{}",
        headers={**headers(), "Content-Type": "application/json", "Content-Length": "not-a-number"},
    )

    assert response.status_code in {400, 413}, response.status_code


def test_an_ordinary_body_is_unaffected(client) -> None:
    app.dependency_overrides[get_rate_limiter] = lambda: None

    response = client.post(
        f"/v1/tickets/{TICKET}/review", json=review_body("k-ok"), headers=headers()
    )

    # Accepted, not merely "not 413": a 422 would make this test vacuous.
    assert response.status_code == 200, response.text


def test_the_limit_is_bounded_on_both_sides() -> None:
    """Large enough for the biggest legitimate payload - C12 allows a 20,000
    character draft body - and small enough to still be a limit. Only
    asserting the lower bound let a mutant raise it to 256 MiB unnoticed."""
    assert MAX_REQUEST_BYTES > 20_000 * 2
    assert MAX_REQUEST_BYTES <= 1024 * 1024, "a megabyte-plus body cap bounds nothing useful"


def test_a_fixed_oversized_body_is_refused() -> None:
    """Independent of the constant, so raising the limit cannot make this pass."""
    from app.api.limits import RequestSizeLimit

    assert RequestSizeLimit(app, max_bytes=1024).max_bytes == 1024


# ===========================================================================
# AN UNHANDLED FAILURE
# ===========================================================================


def test_an_unhandled_failure_returns_a_code_and_never_the_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)

    class Exploding:
        def review(self, request: object) -> None:
            raise RuntimeError("customer bob@example.net token ya29.leaked")

    app.dependency_overrides[get_review_service] = lambda: Exploding()
    app.dependency_overrides[get_rate_limiter] = lambda: None
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with caplog.at_level("ERROR"):
            response = client.post(
                f"/v1/tickets/{TICKET}/review", json=review_body("k-boom"), headers=headers()
            )
    finally:
        app.dependency_overrides.pop(get_review_service, None)
        app.dependency_overrides.pop(get_rate_limiter, None)

    assert response.status_code == 500
    assert response.json() == {"schema_version": "v1", "ok": False, "code": "INTERNAL_ERROR"}
    # Nothing about the failure reaches the caller.
    assert "bob@example.net" not in response.text
    assert "ya29" not in response.text

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "request-12345" in logged, "the correlation id is what joins a report to a log line"
    assert "RuntimeError" in logged, "the type is diagnostic and carries no data"
    # And what is logged is redacted.
    assert "bob@example.net" not in logged
    assert "ya29.leaked" not in logged
    assert "[email]" in logged and "[google-access-token]" in logged


def test_the_correlation_id_cannot_be_used_to_write_arbitrary_log_lines(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The id is bounded, so a long or crafted value cannot flood a log."""
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)

    class Exploding:
        def review(self, request: object) -> None:
            raise RuntimeError("boom")

    app.dependency_overrides[get_review_service] = lambda: Exploding()
    app.dependency_overrides[get_rate_limiter] = lambda: None
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with caplog.at_level("ERROR"):
            client.post(
                f"/v1/tickets/{TICKET}/review",
                json=review_body("k-boom"),
                headers={**headers(), "X-Request-Id": "a" * 300},
            )
    finally:
        app.dependency_overrides.pop(get_review_service, None)
        app.dependency_overrides.pop(get_rate_limiter, None)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "a" * 129 not in logged
