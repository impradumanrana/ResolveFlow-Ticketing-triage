"""Contract tests for the internal mailbox connection endpoints.

The service is injected, so these prove the HTTP boundary - actor requirement,
refusal shape, strict request bodies, fail-closed configuration - without a
database or Google.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.mailboxes import get_connection_service
from app.api.main import app
from app.mailbox.connection import ConnectionOutcome, ConnectionRefused, StartResult

TOKEN = "test-token-not-a-secret"


def headers(**overrides: str | None) -> dict[str, str]:
    base: dict[str, str | None] = {
        "Authorization": f"Bearer {TOKEN}",
        "X-ResolveFlow-Organization-Id": "org-acme",
        "X-ResolveFlow-Membership-Id": "membership-admin",
        "X-ResolveFlow-Role": "ADMIN",
        "X-Request-Id": "request-12345",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value}


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.refuse_start: str | None = None
        self.complete_outcome = ConnectionOutcome(True, "CONNECTED", "mbx-support")

    def start(self, actor, mailbox_id):
        self.calls.append(("start", {"actor": actor, "mailbox_id": mailbox_id}))
        if self.refuse_start:
            raise ConnectionRefused(self.refuse_start)
        return StartResult(
            attempt_id="attempt-1",
            mailbox_id=mailbox_id,
            authorization_url="https://accounts.google.com/o/oauth2/v2/auth?state=x",
            expires_at=datetime(2026, 9, 15, 12, 10, tzinfo=UTC),
        )

    def complete(self, actor, *, state, code):
        self.calls.append(("complete", {"actor": actor, "state": state, "code": code}))
        return self.complete_outcome

    def revoke(self, actor, mailbox_id):
        self.calls.append(("revoke", {"actor": actor, "mailbox_id": mailbox_id}))
        return ConnectionOutcome(True, "REVOKED", mailbox_id)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)
    fake = FakeService()
    app.dependency_overrides[get_connection_service] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_connection_service, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_start_returns_the_authorization_url(service: FakeService, client: TestClient) -> None:
    response = client.post("/v1/mailboxes/mbx-support/connection/start", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["authorization_url"].startswith("https://accounts.google.com/")
    assert body["mailbox_id"] == "mbx-support"
    actor = service.calls[0][1]["actor"]
    assert (actor.membership_id, actor.organization_id, actor.role) == (
        "membership-admin",
        "org-acme",
        "ADMIN",
    )


@pytest.mark.parametrize(
    "path,method",
    [
        ("/v1/mailboxes/mbx-support/connection/start", "post"),
        ("/v1/mailboxes/mbx-support/connection", "delete"),
    ],
)
def test_mailbox_administration_requires_an_actor(
    service: FakeService, client: TestClient, path: str, method: str
) -> None:
    """A mailbox connection is a human decision, never attributed to nobody."""
    response = getattr(client, method)(
        path,
        headers=headers(**{"X-ResolveFlow-Membership-Id": None, "X-ResolveFlow-Role": None}),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_REQUIRED"
    assert service.calls == []


def test_complete_requires_an_actor(service: FakeService, client: TestClient) -> None:
    response = client.post(
        "/v1/mailboxes/connection/complete",
        json={"state": "s" * 32, "code": "code-1"},
        headers=headers(**{"X-ResolveFlow-Membership-Id": None, "X-ResolveFlow-Role": None}),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_REQUIRED"


def test_a_refused_start_is_returned_as_a_403_with_its_code(
    service: FakeService, client: TestClient
) -> None:
    service.refuse_start = "ROLE_CANNOT_ADMINISTER_MAILBOXES"

    response = client.post("/v1/mailboxes/mbx-support/connection/start", headers=headers())

    assert response.status_code == 403
    assert response.json() == {
        "schema_version": "v1",
        "ok": False,
        "code": "ROLE_CANNOT_ADMINISTER_MAILBOXES",
        "mailbox_id": "mbx-support",
    }


def test_complete_forwards_only_state_and_code(service: FakeService, client: TestClient) -> None:
    response = client.post(
        "/v1/mailboxes/connection/complete",
        json={"state": "s" * 32, "code": "code-1"},
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.json()["code"] == "CONNECTED"
    assert set(service.calls[0][1]) == {"actor", "state", "code"}


def test_a_callback_that_names_a_mailbox_is_rejected_before_the_service(
    service: FakeService, client: TestClient
) -> None:
    """Structural wrong-mailbox guard at the HTTP boundary."""
    response = client.post(
        "/v1/mailboxes/connection/complete",
        json={"state": "s" * 32, "code": "code-1", "mailbox_id": "mbx-sales"},
        headers=headers(),
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "body",
    [{"state": "short", "code": "x"}, {"state": "s" * 32, "code": ""}, {"code": "x"}, {}],
)
def test_malformed_completion_bodies_are_rejected(
    service: FakeService, client: TestClient, body: dict
) -> None:
    response = client.post("/v1/mailboxes/connection/complete", json=body, headers=headers())
    assert response.status_code == 422
    assert service.calls == []


def test_a_wrong_account_outcome_is_reported_not_hidden(
    service: FakeService, client: TestClient
) -> None:
    service.complete_outcome = ConnectionOutcome(False, "WRONG_MAILBOX_AUTHORIZED", "mbx-support")

    response = client.post(
        "/v1/mailboxes/connection/complete",
        json={"state": "s" * 32, "code": "code-1"},
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "v1",
        "ok": False,
        "code": "WRONG_MAILBOX_AUTHORIZED",
        "mailbox_id": "mbx-support",
    }


def test_a_stolen_state_outcome_is_an_authorization_failure(
    service: FakeService, client: TestClient
) -> None:
    service.complete_outcome = ConnectionOutcome(False, "ATTEMPT_NOT_YOURS", "mbx-support")

    response = client.post(
        "/v1/mailboxes/connection/complete",
        json={"state": "s" * 32, "code": "code-1"},
        headers=headers(),
    )
    assert response.status_code == 403


def test_revoke_returns_the_outcome(service: FakeService, client: TestClient) -> None:
    response = client.delete("/v1/mailboxes/mbx-support/connection", headers=headers())

    assert response.status_code == 200
    assert response.json()["code"] == "REVOKED"


@pytest.mark.parametrize("mailbox_id", ["x", "has space", "..%2F..%2Fadmin", "a" * 81])
def test_malformed_mailbox_ids_never_reach_the_service(
    service: FakeService, client: TestClient, mailbox_id: str
) -> None:
    response = client.post(f"/v1/mailboxes/{mailbox_id}/connection/start", headers=headers())
    assert response.status_code in (404, 422)
    assert service.calls == []


def test_the_endpoints_still_require_the_service_credential(
    service: FakeService, client: TestClient
) -> None:
    response = client.post(
        "/v1/mailboxes/mbx-support/connection/start",
        headers=headers(Authorization="Bearer wrong-token"),
    )
    assert response.status_code == 401
    assert service.calls == []


def test_unconfigured_mailbox_connection_fails_closed(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)
    for name in (
        "DATABASE_URL",
        "DB_INSTANCE_CONNECTION_NAME",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REDIRECT_URI",
        "MAILBOX_TOKEN_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    app.dependency_overrides.pop(get_connection_service, None)

    response = client.post("/v1/mailboxes/mbx-support/connection/start", headers=headers())

    assert response.status_code == 503


def test_no_mailbox_endpoint_can_send_mail() -> None:
    # The published contract rather than router internals: it is exactly the
    # surface a caller can reach.
    paths = list(app.openapi()["paths"])
    mailbox_paths = [path for path in paths if path.startswith("/v1/mailboxes")]

    assert mailbox_paths
    for path in mailbox_paths:
        for forbidden in ("send", "draft", "message", "label", "modify"):
            assert forbidden not in path.lower(), path
