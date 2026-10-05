"""C12: the internal review endpoints.

A real `ReviewService` over the in-memory store is injected, so these cover the
HTTP boundary and the service together: the status each refusal deserves, the
version a conflict reports back, and that no response ever suggests a send.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.review import DRAFT_MESSAGES, MESSAGES, get_review_service
from app.mailbox.scopes import GMAIL_READONLY
from app.review.drafts import GMAIL_COMPOSE, CreatedDraft
from app.review.memory_store import InMemoryReviewStore
from app.review.records import TicketFacts
from app.review.service import ReviewService

TOKEN = "test-token-not-a-secret"
ORG = "00000000-0000-0000-0000-0000000000a1"
TICKET = "11111111-1111-1111-1111-111111111111"
MAILBOX = "22222222-2222-2222-2222-222222222222"
THREAD = "33333333-3333-3333-3333-333333333333"
AGENT = "44444444-4444-4444-4444-444444444444"
MODEL_DRAFT = "Open the sign-in page and choose Forgot password. [KB-001]"


def headers(role: str = "AGENT", membership: str = AGENT) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-ResolveFlow-Organization-Id": ORG,
        "X-ResolveFlow-Membership-Id": membership,
        "X-ResolveFlow-Role": role,
        "X-Request-Id": "request-12345",
    }


class Composer:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, token, *, raw, thread_id):
        self.calls += 1
        return CreatedDraft(provider_draft_id="draft-1", provider_thread_id=thread_id)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)
    store = InMemoryReviewStore(
        tickets={
            TICKET: TicketFacts(
                ticket_id=TICKET,
                organization_id=ORG,
                mailbox_id=MAILBOX,
                thread_id=THREAD,
                version=3,
                status="WAITING_ON_REVIEW",
                subject="Reset my password",
                customer_address="customer@example.net",
                model_draft=MODEL_DRAFT,
                citations=("KB-001",),
                grounding_validated=True,
                granted_scopes=(GMAIL_READONLY,),
            )
        },
        action_permissions={AGENT: {MAILBOX}},
        inbound={
            THREAD: {
                "from_address": "customer@example.net",
                "subject": "Reset my password",
                "rfc822_message_id": "<first@mail>",
                "to_addresses": [],
                "cc_addresses": [],
            }
        },
        mailbox_addresses={MAILBOX: "support@acme.example"},
    )
    service = ReviewService(store, token_provider=lambda mailbox: "ya29.token")
    app.dependency_overrides[get_review_service] = lambda: service
    yield service, store
    app.dependency_overrides.pop(get_review_service, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def body(**overrides):
    base = {"decision": "RESOLVE", "expected_version": 3, "idempotency_key": "key-12345678"}
    return {**base, **overrides}


def test_a_decision_is_applied_and_reported_plainly(api, client):
    response = client.post(f"/v1/tickets/{TICKET}/review", headers=headers(), json=body())
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True and payload["code"] == "APPLIED"
    assert payload["ticket_version"] == 4
    assert payload["message"] == MESSAGES["APPLIED"]
    assert payload["draft"] is None


def test_approving_a_read_only_mailbox_explains_why_no_draft_exists(api, client):
    response = client.post(
        f"/v1/tickets/{TICKET}/review", headers=headers(), json=body(decision="APPROVE")
    )
    payload = response.json()
    assert payload["ok"] is True
    assert payload["draft"]["status"] == "REFUSED"
    assert payload["draft"]["failure_code"] == "DRAFT_SCOPE_NOT_GRANTED"
    assert payload["draft"]["message"] == DRAFT_MESSAGES["DRAFT_SCOPE_NOT_GRANTED"]
    assert "read-only" in payload["draft"]["message"]


def test_a_created_draft_says_a_person_must_send_it(api, client, monkeypatch):
    service, store = api
    composer = Composer()
    monkeypatch.setattr("app.review.service.composer_for", lambda scopes, transport: composer)
    store.tickets[TICKET] = __import__("dataclasses").replace(
        store.tickets[TICKET], granted_scopes=(GMAIL_READONLY, GMAIL_COMPOSE)
    )
    service.transport = object()

    payload = client.post(
        f"/v1/tickets/{TICKET}/review", headers=headers(), json=body(decision="APPROVE")
    ).json()

    assert payload["draft"]["status"] == "CREATED"
    assert payload["draft"]["provider_draft_id"] == "draft-1"
    assert "person to review and send" in payload["draft"]["message"]
    assert composer.calls == 1


def test_a_version_conflict_is_a_409_that_reports_the_current_version(api, client):
    client.post(
        f"/v1/tickets/{TICKET}/review",
        headers=headers(),
        json=body(decision="REJECT", reason="A person should reply."),
    )
    response = client.post(
        f"/v1/tickets/{TICKET}/review",
        headers=headers(),
        json=body(decision="REJECT", reason="So should I.", idempotency_key="key-87654321"),
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "VERSION_CONFLICT"
    assert payload["ticket_version"] == 4
    assert "Reload" in payload["message"]


def test_a_replay_returns_the_first_outcome(api, client):
    first = client.post(f"/v1/tickets/{TICKET}/review", headers=headers(), json=body()).json()
    again = client.post(f"/v1/tickets/{TICKET}/review", headers=headers(), json=body()).json()
    assert again["replayed"] is True
    assert again["ticket_version"] == first["ticket_version"]


def test_a_conversation_the_person_cannot_act_on_is_404(api, client):
    other = "99999999-9999-9999-9999-999999999999"
    response = client.post(f"/v1/tickets/{other}/review", headers=headers(), json=body())
    assert response.status_code == 404
    assert response.json()["code"] == "TICKET_NOT_FOUND"


def test_a_role_without_the_permission_is_403(api, client):
    response = client.post(f"/v1/tickets/{TICKET}/review", headers=headers("AUDITOR"), json=body())
    assert response.status_code == 403
    assert response.json()["code"] == "ROLE_IS_READ_ONLY"


def test_a_missing_reason_is_refused_with_a_message_worth_reading(api, client):
    response = client.post(
        f"/v1/tickets/{TICKET}/review", headers=headers(), json=body(decision="REJECT")
    )
    assert response.status_code == 403
    assert response.json()["code"] == "REASON_REQUIRED"


def test_a_system_call_without_a_person_cannot_decide(api, client):
    response = client.post(
        f"/v1/tickets/{TICKET}/review",
        headers={
            k: v
            for k, v in headers().items()
            if k not in ("X-ResolveFlow-Membership-Id", "X-ResolveFlow-Role")
        },
        json=body(),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_REQUIRED"


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "SEND", "expected_version": 3, "idempotency_key": "key-12345678"},
        {"decision": "RESOLVE", "expected_version": -1, "idempotency_key": "key-12345678"},
        {"decision": "RESOLVE", "expected_version": 3, "idempotency_key": "short"},
        {"decision": "RESOLVE", "expected_version": 3},
        {
            "decision": "RESOLVE",
            "expected_version": 3,
            "idempotency_key": "key-12345678",
            "assignee_membership_id": "nobody",
        },
        {
            "decision": "RESOLVE",
            "expected_version": 3,
            "idempotency_key": "key-12345678",
            "send_now": True,
        },
    ],
)
def test_a_malformed_decision_is_rejected_by_the_contract(api, client, payload):
    response = client.post(f"/v1/tickets/{TICKET}/review", headers=headers(), json=payload)
    assert response.status_code == 422
    service, store = api
    assert store.actions == [], "a malformed decision reached the service"
    assert store.tickets[TICKET].version == 3


def test_there_is_no_send_decision_in_the_contract():
    contract = json.loads(open("contracts/openapi/v1.json").read())
    decisions = contract["components"]["schemas"]["ReviewRequestBody"]["properties"]["decision"]
    values = decisions.get("enum") or decisions.get("const")
    assert set(values) == {"EDIT", "APPROVE", "REJECT", "REROUTE", "ASSIGN", "RESOLVE"}
    assert "SEND" not in json.dumps(contract["paths"])


def test_the_preview_shows_what_would_be_created_and_what_it_means(api, client):
    response = client.post(f"/v1/tickets/{TICKET}/draft-preview", headers=headers(), json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["to"] == ["customer@example.net"]
    assert payload["from_address"] == "support@acme.example"
    assert payload["subject"] == "Re: Reset my password"
    assert payload["body"] == MODEL_DRAFT
    assert payload["in_reply_to"] == "<first@mail>"
    assert payload["can_create_draft"] is False
    assert "never sends" in payload["effect"]


def test_the_preview_uses_the_text_the_person_is_about_to_approve(api, client):
    payload = client.post(
        f"/v1/tickets/{TICKET}/draft-preview", headers=headers(), json={"body": "My own words."}
    ).json()
    assert payload["body"] == "My own words."


def test_a_preview_that_cannot_be_addressed_says_so(api, client):
    service, store = api
    store.inbound.clear()
    store.tickets[TICKET] = __import__("dataclasses").replace(
        store.tickets[TICKET], customer_address=None
    )
    response = client.post(f"/v1/tickets/{TICKET}/draft-preview", headers=headers(), json={})
    assert response.status_code == 422
    assert response.json()["code"] == "NO_CUSTOMER_ADDRESS"


def test_the_preview_does_not_change_anything(api, client):
    service, store = api
    client.post(f"/v1/tickets/{TICKET}/draft-preview", headers=headers(), json={})
    assert store.actions == [] and store.drafts == [] and store.revisions == []
    assert store.tickets[TICKET].version == 3


def test_review_fails_closed_without_a_database(monkeypatch, client):
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", TOKEN)
    for name in ("DATABASE_URL", "DB_INSTANCE_CONNECTION_NAME", "DB_NAME", "DB_IAM_USER"):
        monkeypatch.delenv(name, raising=False)
    response = client.post(f"/v1/tickets/{TICKET}/review", headers=headers(), json=body())
    assert response.status_code == 503


def test_the_deployed_service_has_no_provider_transport(monkeypatch):
    """The default review service cannot call a provider at all."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user@localhost/db")
    from app.api.review import get_review_service

    service = get_review_service()
    assert service.transport is None
    assert service.token_provider is None
