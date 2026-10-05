"""The pilot stages (C14).

Two stages, and the ladder stops. These tests hold that: the stage is derived
rather than asserted, every scope profile has a decided stage, Observe Mode
means nothing can be written to the mailbox, and no configuration anywhere
produces a stage that sends.
"""

from __future__ import annotations

import pytest

from app.mailbox.scopes import PROFILE_VARIABLE, ScopeProfile
from app.pilot import (
    EXIT_CRITERIA,
    STAGE_FOR_PROFILE,
    PilotStage,
    active_stage,
    describe,
    exit_criteria,
    observing,
)


@pytest.fixture(autouse=True)
def _no_inherited_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's shell must not decide what these tests assert."""
    monkeypatch.delenv(PROFILE_VARIABLE, raising=False)


# ===========================================================================
# THE LADDER HAS TWO RUNGS AND STOPS
# ===========================================================================


def test_there_are_exactly_two_stages() -> None:
    assert set(PilotStage) == {PilotStage.OBSERVE, PilotStage.DRAFT}


def test_no_stage_sends() -> None:
    """Sending is not a later rung; it is absent from the product."""
    for stage in PilotStage:
        assert "SEND" not in stage.value
    for criteria in EXIT_CRITERIA.values():
        for condition in criteria:
            assert "send" not in condition.casefold() or "send themselves" in condition.casefold()


def test_nothing_follows_the_drafting_stage() -> None:
    """Stated as an empty tuple rather than left out, so the absence is a
    decision a reader can see."""
    assert EXIT_CRITERIA[PilotStage.DRAFT] == ()


def test_automatic_sending_is_reported_false_in_every_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for profile in ScopeProfile:
        monkeypatch.setenv(PROFILE_VARIABLE, profile.value)
        assert describe()["automatic_sending"] is False


# ===========================================================================
# THE STAGE IS DERIVED, NOT ASSERTED
# ===========================================================================


def test_a_fresh_deployment_is_observing() -> None:
    assert active_stage() is PilotStage.OBSERVE
    assert observing()
    assert describe()["observe_mode"] is True


def test_the_drafting_profile_is_no_longer_observing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defect this prevents: `observe_mode` was a constant `true`, which
    stopped being true when C13 made drafting possible."""
    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")

    assert active_stage() is PilotStage.DRAFT
    assert not observing()
    assert describe()["observe_mode"] is False


@pytest.mark.parametrize("profile", list(ScopeProfile))
def test_every_scope_profile_has_a_decided_stage(profile: ScopeProfile) -> None:
    """Adding a profile without deciding its stage must fail here rather than
    defaulting to the permissive one."""
    assert profile in STAGE_FOR_PROFILE


def test_the_stage_map_covers_the_profiles_and_nothing_else() -> None:
    assert set(STAGE_FOR_PROFILE) == set(ScopeProfile)


def test_an_unreadable_profile_setting_leaves_the_deployment_observing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo must narrow the posture, never widen it."""
    for value in ("", "   ", "nonsense", "DRAFT", "send"):
        monkeypatch.setenv(PROFILE_VARIABLE, value)
        assert active_stage() is PilotStage.OBSERVE, value


# ===========================================================================
# LEAVING OBSERVE MODE IS A DECISION WITH CONDITIONS
# ===========================================================================


def test_observe_mode_states_what_it_takes_to_leave() -> None:
    criteria = exit_criteria(PilotStage.OBSERVE)

    assert len(criteria) >= 5, "an advance with one condition is not an agreement"
    for condition in criteria:
        assert len(condition) > 40, f"not a usable condition: {condition!r}"


def test_the_conditions_cover_quality_safety_cost_and_authorization() -> None:
    joined = " ".join(exit_criteria(PilotStage.OBSERVE)).casefold()

    assert "quality check" in joined, "no measured quality condition"
    assert "auto_resolve" in joined, "no safety condition"
    assert "budget" in joined or "cost" in joined, "no cost condition"
    assert "authorized" in joined and "compose" in joined, "no client authorization condition"
    # The client's own sample, not ours: otherwise the pilot grades its own work.
    assert "they chose rather than one we chose" in joined


def test_the_criteria_describe_conditions_not_evidence() -> None:
    """Evidence lives in the report; mixing the two lets a condition be
    satisfied by pointing at a file."""
    for stage, criteria in EXIT_CRITERIA.items():
        for condition in criteria:
            assert ".md" not in condition or "docs/" in condition, (stage, condition)


# ===========================================================================
# THE POSTURE IS VISIBLE FROM OUTSIDE
# ===========================================================================


def test_the_capabilities_endpoint_reports_the_real_posture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.api.main import app

    token = "test-token-not-a-secret"
    monkeypatch.setenv("RESOLVEFLOW_INTERNAL_API_TOKEN", token)
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-ResolveFlow-Organization-Id": "acme-support",
        "X-Request-Id": "request-12345",
    }

    body = client.get("/v1/capabilities", headers=headers).json()
    assert body["pilot_stage"] == "OBSERVE"
    assert body["observe_mode"] is True
    assert body["automatic_sending"] is False

    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")
    body = client.get("/v1/capabilities", headers=headers).json()
    assert body["pilot_stage"] == "DRAFT"
    assert body["observe_mode"] is False
    assert body["automatic_sending"] is False, "no posture turns sending on"


def test_the_published_contract_declares_the_stage() -> None:
    import json
    from pathlib import Path

    contract = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts/openapi/v1.json").read_text()
    )
    schema = contract["components"]["schemas"]["CapabilitiesResponse"]["properties"]

    assert schema["pilot_stage"]["enum"] == ["OBSERVE", "DRAFT"]
    # A single-valued literal publishes as `const`, which is the stronger
    # statement: not one of a set of allowed values, but the only one.
    assert schema["automatic_sending"]["const"] is False
    # `observe_mode` is now an ordinary boolean, so a caller reads the posture
    # rather than a constant that was true when it was written.
    assert schema["observe_mode"]["type"] == "boolean"
    assert "const" not in schema["observe_mode"]


# ===========================================================================
# THE DOCUMENT AND THE CODE CANNOT DRIFT
# ===========================================================================


def test_the_uat_document_carries_the_same_exit_criteria_as_the_code() -> None:
    """A condition the client signed that the code does not enforce, or the
    reverse, is how an advance becomes an argument."""
    from pathlib import Path

    document = (Path(__file__).resolve().parents[1] / "docs" / "UAT_CRITERIA.md").read_text(
        encoding="utf-8"
    )

    section = document.split("## 6. Leaving Observe Mode")[1].split("\n## ")[0]
    for condition in EXIT_CRITERIA[PilotStage.OBSERVE]:
        # Compared on the condition's own words, ignoring the line wrapping
        # each file happens to use.
        needle = " ".join(condition.split())
        haystack = " ".join(section.split())
        assert needle in haystack, f"not in docs/UAT_CRITERIA.md §6: {condition}"


def test_the_documents_the_gate_asks_for_exist() -> None:
    """C14's gate is five artifacts. This is the list."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for relative in (
        "docs/UAT_CRITERIA.md",
        "docs/AI_PERFORMANCE_REPORT.md",
        "docs/SUPPORT.md",
        "docs/GO_NO_GO.md",
        "infra/docs/RUNBOOK.md",
        "infra/docs/INCIDENT_RESPONSE.md",
        "docs/PRIVACY.md",
    ):
        path = root / relative
        assert path.exists(), relative
        assert len(path.read_text(encoding="utf-8")) > 2000, f"{relative} is a stub"


def test_the_runbook_covers_the_procedures_support_promises() -> None:
    from pathlib import Path

    runbook = (Path(__file__).resolve().parents[1] / "infra" / "docs" / "RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    for heading in (
        "Answering a data-subject request",
        "Running a retention sweep",
        "Turning the pilot stage",
        "Running the pilot measurements",
        "Rollback",
        "Database restore",
        "Replacing the AI provider key",
        "Rotating the mailbox token key",
    ):
        assert f"## {heading}" in runbook, heading


def test_the_support_document_leaves_contacts_blank_rather_than_inventing_them() -> None:
    """A support plan with placeholder names is not a support plan."""
    from pathlib import Path

    support = (Path(__file__).resolve().parents[1] / "docs" / "SUPPORT.md").read_text(
        encoding="utf-8"
    )

    assert "must be filled in" in support
    # No invented contact details.
    assert "@example.com" not in support
    assert not any(token in support for token in ("+1 555", "555-01", "john.doe"))
    # And it states the single-engineer reality rather than implying a rota.
    assert "no 24/7 rota" in support.casefold() or "single-engineer" in support.casefold()


def test_the_go_no_go_recommendation_is_explicit() -> None:
    from pathlib import Path

    decision = (Path(__file__).resolve().parents[1] / "docs" / "GO_NO_GO.md").read_text(
        encoding="utf-8"
    )

    # The recommendation line itself, not the phrase anywhere in the file:
    # "NO-GO" also appears in the prose further down, which let a mutant
    # rewrite the recommendation while the assertion still passed.
    recommendation = next(
        line for line in decision.splitlines() if "Current recommendation" in line
    )
    assert "NO-GO" in recommendation, recommendation

    # Every item table must name who unblocks its rows, so asserting one
    # occurrence is not enough: a mutant that blanked a single header
    # survived. Section 1 is the exception - its rows are Ready and carry
    # evidence instead.
    item_tables = decision.count("| # | Item | Status |")
    assert item_tables == 7, item_tables
    assert decision.count("| # | Item | Status | Who unblocks |") == item_tables - 1
    assert decision.count("| # | Item | Status | Evidence |") == 1

    assert "no model has ever been called" in decision.casefold()
