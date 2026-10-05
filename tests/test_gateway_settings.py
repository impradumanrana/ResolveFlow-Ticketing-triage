"""C10: what an operator may see and change, and the MVP pipeline on the gateway."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from gateway_fakes import (
    KEY,
    NEW_KEY,
    NOW,
    ORG,
    OTHER_ORG,
    OWNER,
    SECRET,
    classify,
    completion,
    config,
    fail,
    harness,
)

from app.gateway.errors import FailureCode, GatewayFailure
from app.gateway.service import MANAGE_ROLES, VIEW_ROLES, AccessDenied
from app.mailbox.access import Actor

ROOT = Path(__file__).resolve().parents[1]


def actor(role: str, organization: str = ORG, status: str = "ACTIVE") -> Actor:
    return Actor(f"m-{role.lower()}", organization, role, status)


# --------------------------------------------------------------------------
# Roles mirror the web matrix
# --------------------------------------------------------------------------


def roles_with(permission: str) -> set[str]:
    source = (ROOT / "apps/web/src/lib/authz/roles.ts").read_text()
    matrix = source[source.index("MATRIX") :]
    roles = set()
    for role, body in re.findall(r"^  ([A-Z_]+): \[(.*?)\]", matrix, re.MULTILINE | re.DOTALL):
        if f'"{permission}"' in body:
            roles.add(role)
    return roles


def test_python_roles_match_the_web_matrix():
    assert roles_with("ai_settings.view") == VIEW_ROLES
    assert roles_with("ai_settings.manage") == MANAGE_ROLES
    assert MANAGE_ROLES < VIEW_ROLES


@pytest.mark.parametrize("role", ["AGENT", "KNOWLEDGE_MANAGER", "UNKNOWN"])
def test_roles_without_view_permission_are_refused_and_audited(role):
    h = harness()
    with pytest.raises(AccessDenied) as raised:
        h.gateway.describe(actor(role), ORG)
    assert raised.value.code == "ROLE_LACKS_PERMISSION"
    assert h.store.audits[-1]["outcome"] == "DENIED"
    assert h.store.audits[-1]["action"] == "ai_settings.view"


@pytest.mark.parametrize("role", ["SUPERVISOR", "AUDITOR", "AGENT"])
def test_only_owners_and_admins_may_verify_or_replace_keys(role):
    h = harness()
    with pytest.raises(AccessDenied):
        h.gateway.verify(actor(role), ORG, "sim")
    with pytest.raises(AccessDenied):
        h.gateway.replace_credential(actor(role), ORG, "sim", NEW_KEY)
    assert h.adapter.verified == []
    assert h.secrets.versions[SECRET] == [KEY]


def test_another_organization_is_refused_even_for_an_owner():
    h = harness()
    with pytest.raises(AccessDenied) as raised:
        h.gateway.describe(actor("OWNER", organization=OTHER_ORG), ORG)
    assert raised.value.code == "CROSS_ORGANIZATION"


def test_an_inactive_membership_is_refused():
    h = harness()
    with pytest.raises(AccessDenied) as raised:
        h.gateway.verify(actor("OWNER", status="SUSPENDED"), ORG, "sim")
    assert raised.value.code == "MEMBERSHIP_NOT_ACTIVE"


# --------------------------------------------------------------------------
# The masked view
# --------------------------------------------------------------------------


def test_the_view_shows_masked_metadata_only():
    h = harness(
        configs=[
            config(
                credential_last_four="1234",
                monthly_budget_minor_units=5000,
                fallback_classification_model="sim-small-2",
            )
        ]
    )
    classify(h)

    view = h.gateway.describe(actor("AUDITOR"), ORG)
    (provider,) = view.providers
    assert provider.credential_hint == "••••1234"
    assert provider.status == "UNVERIFIED"
    assert provider.region == "eu"
    assert provider.models == {
        "classification": "sim-small",
        "generation": "sim-large",
        "embedding": "sim-embed",
    }
    assert provider.fallback == ("classification: sim-small-2",)
    assert provider.budget_minor_units == 5000
    assert provider.spent_minor_units == pytest.approx(0.024)
    assert provider.reserved_minor_units == 0
    assert "classification: sim-small (eu)" in provider.approved_models

    rendered = repr(view)
    assert KEY not in rendered and SECRET not in rendered
    assert not hasattr(provider, "credential_secret_name")


def test_a_key_that_was_never_verified_shows_no_hint():
    view = harness().gateway.describe(OWNER, ORG)
    assert view.providers[0].credential_hint == "not verified"


def test_recent_failures_are_counted_by_code():
    h = harness(scripts={"sim-small": [fail(FailureCode.RATE_LIMITED, status=429)]})
    with pytest.raises(GatewayFailure):
        classify(h)
    h.secrets.failures[SECRET] = FailureCode.CREDENTIAL_EXPIRED
    h.gateway.credentials.invalidate(SECRET)
    with pytest.raises(GatewayFailure):
        classify(h)
    failures = h.gateway.describe(OWNER, ORG).recent_failures
    # Most frequent first, each with the gateway's own wording.
    assert [(f.code, f.count, f.message) for f in failures] == [
        ("RATE_LIMITED", 2, "The provider is rate limiting requests."),
        ("CREDENTIAL_EXPIRED", 1, "The stored API key version is disabled or destroyed."),
    ]


def test_failures_older_than_a_day_are_not_counted():
    h = harness(scripts={"sim-small": [fail(FailureCode.QUOTA_EXHAUSTED, status=429)]})
    with pytest.raises(GatewayFailure):
        classify(h)
    h.clock.now = NOW + __import__("datetime").timedelta(hours=25)
    assert h.gateway.describe(OWNER, ORG).recent_failures == ()


def test_successful_fallbacks_are_counted_for_the_operator():
    h = harness(
        configs=[config(fallback_classification_model="sim-small-2")],
        scripts={"sim-small": [fail(FailureCode.PROVIDER_UNAVAILABLE, status=503)]},
    )
    assert h.gateway.describe(OWNER, ORG).fallback_uses == 0
    classify(h)
    classify(h)
    assert h.gateway.describe(OWNER, ORG).fallback_uses == 2


def test_a_healthy_verified_provider_says_so():
    h = harness()
    h.gateway.verify(OWNER, ORG, "sim")
    assert h.gateway.describe(OWNER, ORG).providers[0].status == "HEALTHY"


# --------------------------------------------------------------------------
# Server-side verification
# --------------------------------------------------------------------------


def test_verification_checks_every_configured_model_and_generates_nothing():
    h = harness(configs=[config(fallback_classification_model="sim-small-2")])
    report = h.gateway.verify(OWNER, ORG, "sim")

    assert report.ok and report.code is None
    assert report.checked_models == ("sim-small", "sim-small-2", "sim-large", "sim-embed")
    assert h.adapter.calls == [], "verification must not generate"
    assert {c.purpose for c in h.store.calls} == {"VERIFY"}
    assert all(c.reservation_state == "NONE" and c.cost_micro == 0 for c in h.store.calls)
    assert h.store.configs[0].last_verified_at == NOW
    assert h.store.audits[-1]["action"] == "ai_provider.verify"
    assert h.store.audits[-1]["outcome"] == "ALLOWED"


def test_a_failed_verification_records_only_a_code():
    h = harness()
    h.adapter.verify_script[KEY] = fail(FailureCode.CREDENTIAL_INVALID, status=401)
    report = h.gateway.verify(OWNER, ORG, "sim")

    assert (report.ok, report.code) == (False, "CREDENTIAL_INVALID")
    assert h.store.verification_errors[(ORG, "sim")] == "CREDENTIAL_INVALID"
    assert h.store.configs[0].last_verified_at is None
    assert h.store.audits[-1]["outcome"] == "FAILED"
    assert h.store.calls[-1].failure_code == "CREDENTIAL_INVALID"


def test_verification_refuses_an_unapproved_configured_model_without_calling():
    h = harness(configs=[config(generation_model="sim-unapproved")])
    report = h.gateway.verify(OWNER, ORG, "sim")
    assert report.code == "MODEL_NOT_APPROVED"
    assert h.adapter.verified == []


def test_verification_of_an_unreadable_key_reports_the_secret_problem():
    h = harness()
    h.secrets.failures[SECRET] = FailureCode.CREDENTIAL_EXPIRED
    assert h.gateway.verify(OWNER, ORG, "sim").code == "CREDENTIAL_EXPIRED"
    assert h.adapter.verified == []


def test_verifying_an_unknown_provider_is_a_configuration_failure():
    with pytest.raises(GatewayFailure) as raised:
        harness().gateway.verify(OWNER, ORG, "nobody")
    assert raised.value.code is FailureCode.CONFIG_MISSING


def test_an_adapter_crash_during_verification_is_contained():
    h = harness()
    h.adapter.verify_script[KEY] = RuntimeError(f"boom {KEY}")
    report = h.gateway.verify(OWNER, ORG, "sim")
    assert report.code == "PROVIDER_UNAVAILABLE"
    assert KEY not in repr(h.store.calls)


# --------------------------------------------------------------------------
# Replacing the key
# --------------------------------------------------------------------------


def test_a_working_key_is_verified_then_stored_and_used_immediately():
    h = harness()
    classify(h)  # caches the old key
    report = h.gateway.replace_credential(OWNER, ORG, "sim", f"  {NEW_KEY}\n")

    assert report.ok and report.stored
    assert h.secrets.versions[SECRET] == [KEY, NEW_KEY]
    assert [key for _, key in h.adapter.verified] == [NEW_KEY] * 3, "checked before storing"
    assert h.store.configs[0].credential_last_four == NEW_KEY[-4:]
    classify(h)
    assert h.adapter.calls[-1]["key"] == NEW_KEY, "the cache was not refreshed"

    audit = h.store.audits[-1]
    assert (audit["action"], audit["outcome"]) == ("ai_provider.credential.replace", "ALLOWED")
    assert audit["metadata"]["hint"] == f"••••{NEW_KEY[-4:]}"
    assert NEW_KEY not in repr(h.store.audits)


def test_a_key_the_provider_rejects_is_never_stored():
    h = harness()
    h.adapter.verify_script[NEW_KEY] = fail(FailureCode.CREDENTIAL_INVALID, status=401)
    report = h.gateway.replace_credential(OWNER, ORG, "sim", NEW_KEY)

    assert (report.ok, report.stored, report.code) == (False, False, "CREDENTIAL_INVALID")
    assert h.secrets.versions[SECRET] == [KEY], "the working key was replaced by a bad one"
    assert h.store.configs[0].credential_last_four is None
    classify(h)
    assert h.adapter.calls[-1]["key"] == KEY
    assert h.store.audits[-1]["outcome"] == "FAILED"


def test_a_malformed_key_is_refused_before_the_provider_sees_it():
    h = harness()
    report = h.gateway.replace_credential(OWNER, ORG, "sim", "not a key")
    assert report.code == "CREDENTIAL_INVALID"
    assert h.adapter.verified == []
    assert h.secrets.versions[SECRET] == [KEY]


def test_a_key_that_cannot_be_written_is_reported_and_not_half_applied():
    h = harness()
    h.secrets.add_failure = FailureCode.CREDENTIAL_ACCESS_DENIED
    report = h.gateway.replace_credential(OWNER, ORG, "sim", NEW_KEY)
    assert (report.ok, report.stored, report.code) == (False, False, "CREDENTIAL_ACCESS_DENIED")
    assert h.store.configs[0].credential_last_four is None


def test_a_key_is_checked_against_every_configured_model():
    h = harness(configs=[config(generation_model="sim-large")])
    h.adapter.verify_script[NEW_KEY] = None
    original = h.adapter.verify

    def refuse_generation(credential, *, model, region, timeout):
        if model == "sim-large":
            raise fail(FailureCode.PERMISSION_DENIED, status=403)
        return original(credential, model=model, region=region, timeout=timeout)

    h.adapter.verify = refuse_generation  # type: ignore[method-assign]
    report = h.gateway.replace_credential(OWNER, ORG, "sim", NEW_KEY)
    assert report.code == "PERMISSION_DENIED"
    assert report.checked_models == ("sim-small",)
    assert h.secrets.versions[SECRET] == [KEY]


# --------------------------------------------------------------------------
# The MVP triage graph on the gateway
# --------------------------------------------------------------------------


def run_graph(
    h, ticket_subject="App crash", ticket_body="My app crashes when I open the dashboard."
):
    from test_rules_and_eval import StaticMCP

    from app.gateway.mvp_provider import GatewayProvider
    from app.graph import build_workflow
    from app.models import Ticket

    provider = GatewayProvider(h.gateway, ORG, correlation_id="ticket-1")
    mcp = StaticMCP()
    ticket = Ticket(ticket_id="T-1", subject=ticket_subject, body=ticket_body)
    state = build_workflow(provider, mcp).invoke(
        {"ticket": ticket, "provider": provider, "mcp_client": mcp, "trace": []}
    )
    return state["result"], provider


def grounded(article_id_key: str = "E1") -> str:
    return (
        '{"answer": "Verified support steps. ['
        + article_id_key
        + ']", "citations": ["'
        + article_id_key
        + '"], "claims": [{"claim": "Verified support steps.", "evidence_key": "'
        + article_id_key
        + '", "support_quote": "Verified support steps."}], '
        '"sufficient_evidence": true}'
    )


def test_the_proven_pipeline_runs_unchanged_through_the_gateway(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-developer-environment-key-0000")
    h = harness(scripts={"sim-large": [completion(grounded())]})

    result, provider = run_graph(h)

    assert result.route == "AUTO_RESOLVE"
    assert "Verified support steps. [KB-TEST]" in result.draft
    assert result.citations == ["KB-TEST"]
    assert result.grounding_validated is True
    assert [c["model"] for c in h.adapter.calls] == ["sim-small", "sim-large"]
    assert {c["key"] for c in h.adapter.calls} == {KEY}
    assert provider.model == "sim/sim-large"
    assert {c.correlation_id for c in h.store.calls} == {"ticket-1"}
    # The MVP's own prompts were used, not new ones.
    assert "Classify an incoming customer-support ticket" in h.adapter.calls[0]["request"].system
    assert "using ONLY the supplied approved evidence" in h.adapter.calls[1]["request"].system


@pytest.mark.parametrize(
    "setup",
    [
        lambda h: h.secrets.failures.__setitem__(SECRET, FailureCode.CREDENTIAL_EXPIRED),
        lambda h: h.adapter.scripts.__setitem__(
            "sim-small", [fail(FailureCode.RATE_LIMITED, status=429)]
        ),
        lambda h: h.adapter.scripts.__setitem__("sim-small", [completion("nope")]),
        lambda h: setattr(
            h.store, "configs", [replace(h.store.configs[0], monthly_budget_minor_units=0)]
        ),
    ],
    ids=["expired-key", "rate-limited", "invalid-schema", "budget"],
)
def test_a_gateway_failure_sends_the_ticket_to_a_person(setup):
    h = harness()
    setup(h)
    result, provider = run_graph(h)

    assert result.route == "ESCALATE"
    assert "MODEL_ERROR" in result.rule_codes
    # The escalation path writes an internal note, never a customer answer.
    assert result.draft.startswith("Human review required")
    assert result.citations == [] and result.grounding_validated is False
    assert provider.last_failure is not None
    assert provider.health_check() is False
    classify_trace = next(e for e in result.trace if e.node == "classify")
    assert classify_trace.status == "error"


def test_a_generation_failure_after_classification_still_goes_to_a_person():
    h = harness(scripts={"sim-large": [fail(FailureCode.QUOTA_EXHAUSTED, status=429)]})
    result, provider = run_graph(h)
    # The MVP's grounding validator fails closed on a generation error.
    assert result.route == "ESCALATE"
    assert "GROUNDING_VALIDATION_FAILED" in result.rule_codes
    assert result.grounding_validated is False and result.citations == []
    assert result.draft.startswith("Human review required")
    assert provider.last_failure.code is FailureCode.QUOTA_EXHAUSTED


def test_an_unapproved_generation_fallback_is_not_used_by_the_pipeline():
    # sim-small-2 is approved only for classification.
    h = harness(
        configs=[config(fallback_generation_model="sim-small-2")],
        scripts={"sim-large": [fail(FailureCode.PROVIDER_UNAVAILABLE, status=503)]},
    )
    result, provider = run_graph(h)
    assert result.route == "ESCALATE"
    assert "GROUNDING_VALIDATION_FAILED" in result.rule_codes
    assert "sim-small-2" not in [c["model"] for c in h.adapter.calls]
    assert provider.last_failure.code is FailureCode.PROVIDER_UNAVAILABLE
    assert provider.rule_codes == ()


def test_an_approved_generation_fallback_answers_and_says_so():
    from gateway_fakes import approvals

    from app.gateway.contract import Operation
    from app.gateway.policy import ModelApproval

    h = harness(
        configs=[config(fallback_generation_model="sim-large-2")],
        approved=approvals()
        + [ModelApproval("sim", "sim-large-2", Operation.GENERATION, "eu", 100, 400, 1500)],
        scripts={
            "sim-large": [fail(FailureCode.PROVIDER_UNAVAILABLE, status=503)],
            "sim-large-2": [completion(grounded())],
        },
    )
    result, provider = run_graph(h)
    assert result.route == "AUTO_RESOLVE" and result.grounding_validated is True
    assert provider.model == "sim/sim-large-2"
    assert provider.rule_codes == ("PROVIDER_FALLBACK_USED",)
    assert h.store.calls[-1].fallback_from == "sim/sim-large"


@pytest.mark.parametrize(
    ("previous", "cleared"),
    [
        (FailureCode.CREDENTIAL_INVALID, True),
        (FailureCode.CREDENTIAL_EXPIRED, True),
        (FailureCode.PERMISSION_DENIED, True),
        (FailureCode.BUDGET_EXCEEDED, False),
        (FailureCode.QUOTA_EXHAUSTED, False),
        (FailureCode.RATE_LIMITED, False),
        (FailureCode.INVALID_SCHEMA, False),
    ],
)
def test_a_passing_check_clears_only_what_it_proved(previous, cleared):
    h = harness(configs=[config(last_failure_code=previous.value)])
    h.gateway.verify(OWNER, ORG, "sim")
    view = h.gateway.describe(OWNER, ORG).providers[0]
    assert (view.last_failure_code is None) is cleared
    assert view.status == ("HEALTHY" if cleared else "FAILING")


def test_a_stored_replacement_key_clears_a_key_failure():
    h = harness(configs=[config(last_failure_code="CREDENTIAL_INVALID")])
    h.gateway.replace_credential(OWNER, ORG, "sim", NEW_KEY)
    assert h.store.configs[0].last_failure_code is None


def test_a_failing_check_clears_nothing():
    h = harness(configs=[config(last_failure_code="CREDENTIAL_EXPIRED")])
    h.adapter.verify_script[KEY] = fail(FailureCode.CREDENTIAL_INVALID, status=401)
    h.gateway.verify(OWNER, ORG, "sim")
    assert h.store.configs[0].last_failure_code == "CREDENTIAL_EXPIRED"
