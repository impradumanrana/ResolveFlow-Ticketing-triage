from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.contracts import API_VERSION
from app.api.main import app
from app.api.service import get_triage_service
from app.models import Ticket, TriageResult

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_CONTRACT = ROOT / "contracts" / "openapi" / "v1.json"


class FakeTriageService:
    def triage(self, ticket: Ticket) -> TriageResult:
        return TriageResult(
            ticket_id=ticket.ticket_id,
            route="ESCALATE",
            category="technical",
            urgency="medium",
            confidence=0.9,
            queue="Human Triage",
            decision_summary="Contract fixture routes safely to review.",
            rule_codes=["LOW_KB_CONFIDENCE"],
            draft="Human review required.",
            trace=[],
        )


def test_public_health_is_minimal():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "resolveflow-ai-api",
        "version": API_VERSION,
    }


def test_protected_route_fails_closed_without_configuration(monkeypatch):
    monkeypatch.delenv("RESOLVEFLOW_INTERNAL_API_TOKEN", raising=False)
    response = TestClient(app).get(
        "/v1/capabilities",
        headers={
            "Authorization": "Bearer any-value",
            "X-ResolveFlow-Organization-Id": "client-one",
            "X-Request-Id": "request-123",
        },
    )
    assert response.status_code == 503


def test_triage_contract_uses_header_organization_and_injected_service(monkeypatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", "test-token-not-a-secret")
    app.dependency_overrides[get_triage_service] = FakeTriageService
    try:
        response = TestClient(app).post(
            "/v1/triage",
            headers={
                "Authorization": "Bearer test-token-not-a-secret",
                "X-ResolveFlow-Organization-Id": "client-one",
                "X-Request-Id": "request-123",
            },
            json={
                "ticket": {
                    "ticket_id": "C01-001",
                    "subject": "Foundation contract test",
                    "body": "Do not call an external provider.",
                }
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "v1"
    assert body["organization_id"] == "client-one"
    assert body["request_id"] == "request-123"
    assert body["result"]["route"] == "ESCALATE"


def test_browser_cannot_choose_organization_in_request_body(monkeypatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", "test-token-not-a-secret")
    response = TestClient(app).post(
        "/v1/triage",
        headers={
            "Authorization": "Bearer test-token-not-a-secret",
            "X-ResolveFlow-Organization-Id": "client-one",
            "X-Request-Id": "request-123",
        },
        json={
            "organization_id": "another-client",
            "ticket": {
                "ticket_id": "C01-002",
                "subject": "Unexpected tenant field",
                "body": "This must be rejected.",
            },
        },
    )
    assert response.status_code == 422


def test_checked_in_openapi_contract_has_no_drift():
    expected = json.loads(OPENAPI_CONTRACT.read_text())
    assert expected == app.openapi()
    assert "/v1/triage" in expected["paths"]
    assert not any("send" in path.casefold() for path in expected["paths"])


# ---------------------------------------------------------------------------
# C03: server-derived actor context on the internal boundary
# ---------------------------------------------------------------------------


def _internal_headers(**overrides: str) -> dict[str, str]:
    headers = {
        "Authorization": "Bearer test-token-not-a-secret",
        "X-ResolveFlow-Organization-Id": "client-one",
        "X-ResolveFlow-Membership-Id": "membership-1",
        "X-ResolveFlow-Role": "AGENT",
        "X-Request-Id": "request-1234",
    }
    headers.update(overrides)
    return {key: value for key, value in headers.items() if value}


def test_actor_context_is_accepted_when_well_formed(monkeypatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", "test-token-not-a-secret")
    response = TestClient(app).get("/v1/capabilities", headers=_internal_headers())
    assert response.status_code == 200


def test_an_unrecognised_actor_role_is_refused(monkeypatch):
    """A role the product does not define must never be treated as valid."""
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", "test-token-not-a-secret")
    for role in ("SUPERUSER", "owner; drop table users", "ROOT", "admin\nOWNER"):
        response = TestClient(app).get(
            "/v1/capabilities", headers=_internal_headers(**{"X-ResolveFlow-Role": role})
        )
        assert response.status_code == 400, role


def test_actor_membership_and_role_must_arrive_together(monkeypatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", "test-token-not-a-secret")
    client = TestClient(app)

    only_role = _internal_headers()
    del only_role["X-ResolveFlow-Membership-Id"]
    assert client.get("/v1/capabilities", headers=only_role).status_code == 400

    only_membership = _internal_headers()
    del only_membership["X-ResolveFlow-Role"]
    assert client.get("/v1/capabilities", headers=only_membership).status_code == 400


def test_system_initiated_calls_may_omit_the_actor(monkeypatch):
    """Scheduled work must not have to impersonate a person to run."""
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", "test-token-not-a-secret")
    headers = _internal_headers()
    del headers["X-ResolveFlow-Membership-Id"]
    del headers["X-ResolveFlow-Role"]

    assert TestClient(app).get("/v1/capabilities", headers=headers).status_code == 200


def test_a_malformed_membership_id_is_refused(monkeypatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", "test-token-not-a-secret")
    for membership_id in ("a", "'; drop table memberships; --", "x" * 200, "has space"):
        response = TestClient(app).get(
            "/v1/capabilities",
            headers=_internal_headers(**{"X-ResolveFlow-Membership-Id": membership_id}),
        )
        assert response.status_code == 400, membership_id


def test_actor_context_never_substitutes_for_the_service_credential(monkeypatch):
    """Identity headers are context, not authentication."""
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", "test-token-not-a-secret")
    headers = _internal_headers(Authorization="Bearer wrong-token")
    assert TestClient(app).get("/v1/capabilities", headers=headers).status_code == 401

    no_auth = _internal_headers()
    del no_auth["Authorization"]
    assert TestClient(app).get("/v1/capabilities", headers=no_auth).status_code == 401
