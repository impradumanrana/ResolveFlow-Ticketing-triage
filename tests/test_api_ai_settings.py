"""C10: the internal AI settings endpoints.

A real `AIGateway` over the in-memory store is injected, so these prove the
HTTP boundary and the service together: masking, roles, refusal shapes, and
that no key is ever repeated in any response - including validation errors.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from gateway_fakes import KEY, NEW_KEY, ORG, SECRET, config, fail, harness

from app.api.ai_settings import get_gateway
from app.api.main import app
from app.gateway.errors import FailureCode

TOKEN = "test-token-not-a-secret"


def headers(role: str = "OWNER", organization: str = ORG) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-ResolveFlow-Organization-Id": organization,
        "X-ResolveFlow-Membership-Id": f"m-{role.lower()}",
        "X-ResolveFlow-Role": role,
        "X-Request-Id": "request-12345",
    }


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)
    state = harness(configs=[config(credential_last_four=KEY[-4:])])
    app.dependency_overrides[get_gateway] = lambda: state.gateway
    yield state
    app.dependency_overrides.pop(get_gateway, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def no_secrets(response) -> None:
    body = response.text
    assert KEY not in body and NEW_KEY not in body and SECRET not in body


def test_settings_are_masked(h, client):
    response = client.get("/v1/ai/settings", headers=headers("AUDITOR"))
    assert response.status_code == 200
    body = response.json()
    (provider,) = body["providers"]
    assert provider["credential_hint"] == f"••••{KEY[-4:]}"
    assert provider["region"] == "eu"
    assert provider["status"] == "UNVERIFIED"
    assert "not a balance" in body["cost_note"]
    assert body["fallback_uses"] == 0
    assert set(provider) == {
        "provider",
        "status",
        "credential_hint",
        "region",
        "models",
        "fallback",
        "approved_models",
        "budget_minor_units",
        "currency",
        "spent_minor_units",
        "reserved_minor_units",
        "last_verified_at",
        "last_failure_code",
        "last_failure_message",
        "last_failure_at",
    }
    no_secrets(response)


@pytest.mark.parametrize("role", ["AGENT", "KNOWLEDGE_MANAGER"])
def test_roles_without_permission_get_a_refusal(h, client, role):
    response = client.get("/v1/ai/settings", headers=headers(role))
    assert response.status_code == 403
    assert response.json() == {"schema_version": "v1", "ok": False, "code": "ROLE_LACKS_PERMISSION"}
    assert h.store.audits[-1]["outcome"] == "DENIED"


def test_system_calls_without_a_person_are_refused(h, client):
    response = client.get(
        "/v1/ai/settings",
        headers={
            k: v
            for k, v in headers().items()
            if k not in ("X-ResolveFlow-Membership-Id", "X-ResolveFlow-Role")
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_REQUIRED"


def test_verify_reports_the_outcome_with_a_plain_message(h, client):
    h.adapter.verify_script[KEY] = fail(FailureCode.CREDENTIAL_INVALID, status=401)
    response = client.post("/v1/ai/providers/sim/verify", headers=headers("ADMIN"))
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "v1",
        "provider": "sim",
        "ok": False,
        "stored": False,
        "code": "CREDENTIAL_INVALID",
        "message": "The provider rejected the API key.",
        "checked_models": [],
    }


def test_supervisors_may_look_but_not_verify(h, client):
    assert client.get("/v1/ai/settings", headers=headers("SUPERVISOR")).status_code == 200
    response = client.post("/v1/ai/providers/sim/verify", headers=headers("SUPERVISOR"))
    assert response.status_code == 403
    assert h.adapter.verified == []


def test_an_unknown_provider_is_not_found(h, client):
    response = client.post("/v1/ai/providers/nobody/verify", headers=headers())
    assert response.status_code == 404
    assert response.json()["code"] == "CONFIG_MISSING"


@pytest.mark.parametrize("provider", ["Sim", "a", "sim/../x", "x" * 40, "sim%20x"])
def test_provider_names_are_constrained(h, client, provider):
    response = client.post(f"/v1/ai/providers/{provider}/verify", headers=headers())
    assert response.status_code in (404, 422)
    assert h.adapter.verified == []


def test_a_working_key_is_stored_and_never_echoed(h, client):
    response = client.put(
        "/v1/ai/providers/sim/credential", headers=headers(), json={"api_key": NEW_KEY}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True and response.json()["stored"] is True
    assert h.secrets.versions[SECRET][-1] == NEW_KEY
    no_secrets(response)


def test_a_rejected_key_is_not_stored_and_not_echoed(h, client):
    h.adapter.verify_script[NEW_KEY] = fail(FailureCode.CREDENTIAL_INVALID, status=401)
    response = client.put(
        "/v1/ai/providers/sim/credential", headers=headers(), json={"api_key": NEW_KEY}
    )
    assert response.json()["code"] == "CREDENTIAL_INVALID"
    assert h.secrets.versions[SECRET] == [KEY]
    no_secrets(response)


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": NEW_KEY, "provider": "other"},
        {"api_key": NEW_KEY, "secret_name": "projects/x/secrets/y"},
        {"api_key": [NEW_KEY]},
        {"api_key": {"value": NEW_KEY}},
        {"key": NEW_KEY},
    ],
)
def test_malformed_bodies_are_rejected_without_repeating_the_key(h, client, payload):
    response = client.put("/v1/ai/providers/sim/credential", headers=headers(), json=payload)
    assert response.status_code == 422
    no_secrets(response)
    assert all(set(item) == {"loc", "type"} for item in response.json()["detail"])
    assert h.secrets.versions[SECRET] == [KEY]


def test_a_body_that_is_not_json_is_rejected_without_repeating_it(h, client):
    response = client.put(
        "/v1/ai/providers/sim/credential",
        headers={**headers(), "Content-Type": "application/json"},
        content=f'{{"api_key": "{NEW_KEY}"',
    )
    assert response.status_code == 422
    no_secrets(response)


def test_the_oauth_code_is_no_longer_repeated_in_mailbox_validation_errors(h, client):
    from app.api.mailboxes import get_connection_service

    app.dependency_overrides[get_connection_service] = lambda: object()
    code = "4/0A-very-sensitive-authorization-code" + "x" * 3000
    response = client.post(
        "/v1/mailboxes/connection/complete",
        headers=headers(),
        json={"state": "s" * 32, "code": code},
    )
    app.dependency_overrides.pop(get_connection_service, None)
    assert response.status_code == 422
    assert response.json()["detail"] == [{"loc": ["body", "code"], "type": "string_too_long"}]
    assert "very-sensitive" not in response.text


def test_problems_and_fallbacks_are_reported_with_their_own_words(h, client):
    from gateway_fakes import classify as run

    from app.gateway.errors import GatewayFailure as Failure

    h.store.configs = [
        config(credential_last_four=KEY[-4:], fallback_classification_model="sim-small-2")
    ]
    h.adapter.scripts["sim-small"] = [fail(FailureCode.RATE_LIMITED, status=429)]
    run(h)  # falls back and succeeds
    h.adapter.scripts["sim-small-2"] = [fail(FailureCode.QUOTA_EXHAUSTED, status=429)]
    with pytest.raises(Failure):
        run(h)

    body = client.get("/v1/ai/settings", headers=headers()).json()
    assert body["fallback_uses"] == 1
    assert body["recent_failures"][0] == {
        "code": "RATE_LIMITED",
        "count": 4,
        "message": "The provider is rate limiting requests.",
    }
    assert {f["code"] for f in body["recent_failures"]} == {"RATE_LIMITED", "QUOTA_EXHAUSTED"}


def test_the_published_contract_describes_the_endpoints_without_secrets():
    contract = json.loads(open("contracts/openapi/v1.json").read())
    paths = contract["paths"]
    assert set(paths["/v1/ai/settings"]) == {"get"}
    assert set(paths["/v1/ai/providers/{provider}/verify"]) == {"post"}
    assert set(paths["/v1/ai/providers/{provider}/credential"]) == {"put"}
    request = contract["components"]["schemas"]["AiCredentialReplaceRequest"]
    assert request["properties"]["api_key"]["format"] == "password"
    assert request["properties"]["api_key"]["writeOnly"] is True
    view = contract["components"]["schemas"]["AiProviderView"]["properties"]
    assert not {"credential_secret_name", "api_key", "secret"} & set(view)


def test_the_gateway_dependency_fails_closed_without_a_database(monkeypatch, client):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)
    for name in ("DATABASE_URL", "DB_INSTANCE_CONNECTION_NAME", "DB_NAME", "DB_IAM_USER"):
        monkeypatch.delenv(name, raising=False)
    response = client.get("/v1/ai/settings", headers=headers())
    assert response.status_code == 503
