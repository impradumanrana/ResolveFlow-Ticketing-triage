"""C11: the internal Quality Check endpoint."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.quality import VIEW_ROLES, get_engine

TOKEN = "test-token-not-a-secret"
ORG = "00000000-0000-0000-0000-0000000000a1"
ROOT = Path(__file__).resolve().parents[1]
RUN = {
    "id": "55555555-6666-7777-8888-999999999999",
    "dataset_version": "v1",
    "threshold_version": "v1",
    "knowledge_fingerprint": "corpus-abc",
    "article_count": 12,
    "status": "COMPLETED",
    "passed": True,
    "started_at": datetime(2026, 9, 17, 9, 0, tzinfo=UTC),
    "completed_at": datetime(2026, 9, 17, 9, 2, tzinfo=UTC),
    "results": [
        {
            "gate": "safety",
            "metric": "guardrail_recall",
            "value": 1,
            "threshold": 0.9,
            "passed": True,
            "detail": {"direction": "min", "auto_resolved_risky": []},
        },
        {
            "gate": "latency",
            "metric": "p95_ms",
            "value": -1,
            "threshold": 20000,
            "passed": False,
            "detail": {"direction": "max"},
        },
    ],
}


def headers(role: str = "SUPERVISOR") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-ResolveFlow-Organization-Id": ORG,
        "X-ResolveFlow-Membership-Id": f"m-{role.lower()}",
        "X-ResolveFlow-Role": role,
        "X-Request-Id": "request-12345",
    }


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)
    state: dict[str, Any] = {"run": RUN, "fingerprint": "corpus-abc"}

    class FakeStore:
        def __init__(self, engine: Any, organization_id: str) -> None:
            state["organization_id"] = organization_id

        def latest(self) -> dict[str, Any] | None:
            return state["run"]

    monkeypatch.setattr("app.triage.quality_store.PostgresQualityStore", FakeStore)
    monkeypatch.setattr(
        "app.knowledge.repository.knowledge_fingerprint",
        lambda connection, org: state["fingerprint"],
    )

    class Connection:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    app.dependency_overrides[get_engine] = lambda: type(
        "Engine", (), {"connect": lambda self: Connection()}
    )()
    yield state
    app.dependency_overrides.pop(get_engine, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_the_latest_run_is_returned_with_its_gates(api, client):
    body = client.get("/v1/quality/latest", headers=headers()).json()

    assert body["present"] is True and body["passed"] is True
    assert body["dataset_version"] == "v1" and body["article_count"] == 12
    assert body["corpus_changed_since_run"] is False
    assert body["gates"][0] == {
        "gate": "safety",
        "metric": "guardrail_recall",
        "value": 1.0,
        "threshold": 0.9,
        "direction": "min",
        "passed": True,
        "detail": {"auto_resolved_risky": []},
    }
    assert api["organization_id"] == ORG


def test_a_metric_that_could_not_be_measured_is_reported_as_unmeasured(api, client):
    body = client.get("/v1/quality/latest", headers=headers()).json()
    latency = next(gate for gate in body["gates"] if gate["metric"] == "p95_ms")
    assert latency["value"] is None and latency["passed"] is False
    assert latency["direction"] == "max"


def test_a_changed_corpus_is_reported(api, client):
    api["fingerprint"] = "corpus-def"
    body = client.get("/v1/quality/latest", headers=headers()).json()
    assert body["corpus_changed_since_run"] is True
    assert body["knowledge_fingerprint"] == "corpus-abc"
    assert body["current_knowledge_fingerprint"] == "corpus-def"


def test_never_having_run_is_not_an_error(api, client):
    api["run"] = None
    body = client.get("/v1/quality/latest", headers=headers()).json()
    assert body["present"] is False and body["gates"] == []
    assert body["current_knowledge_fingerprint"] == "corpus-abc"


@pytest.mark.parametrize("role", ["AGENT"])
def test_a_role_without_the_permission_is_refused(api, client, role):
    response = client.get("/v1/quality/latest", headers=headers(role))
    assert response.status_code == 403
    assert response.json()["code"] == "ROLE_LACKS_PERMISSION"


def test_a_system_call_without_a_person_is_refused(api, client):
    response = client.get(
        "/v1/quality/latest",
        headers={
            k: v
            for k, v in headers().items()
            if k not in ("X-ResolveFlow-Membership-Id", "X-ResolveFlow-Role")
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_REQUIRED"


def test_the_python_roles_match_the_web_matrix():
    source = (ROOT / "apps/web/src/lib/authz/roles.ts").read_text()
    matrix = source[source.index("MATRIX") :]
    roles = {
        role
        for role, body in re.findall(r"^  ([A-Z_]+): \[(.*?)\]", matrix, re.MULTILINE | re.DOTALL)
        if '"quality.view"' in body
    }
    assert roles == VIEW_ROLES


def test_the_endpoint_fails_closed_without_a_database(monkeypatch, client):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)
    for name in ("DATABASE_URL", "DB_INSTANCE_CONNECTION_NAME", "DB_NAME", "DB_IAM_USER"):
        monkeypatch.delenv(name, raising=False)
    assert client.get("/v1/quality/latest", headers=headers()).status_code == 503
