"""The scope change: compose becomes askable, sending stays impossible.

The client authorized adding `gmail.compose` to the consent request so an
approved answer can become a draft in their own mailbox (C12). These tests hold
the three properties that made it safe to do:

* the read-only deployment is **unchanged** - same consent request, same
  refusals, including for compose itself;
* compose is **optional** - a person who unticks it on Google's consent screen
  still connects the mailbox, read-only;
* every other write-capable scope is **still refused**, under both profiles,
  at the policy and at the database.
"""

from __future__ import annotations

import pytest

from app.mailbox.scopes import (
    EMAIL,
    GMAIL_COMPOSE,
    GMAIL_READONLY,
    NEVER_PERMITTED_SCOPES,
    OPENID,
    PROFILE_VARIABLE,
    REQUIRED_SCOPES,
    WRITE_CAPABLE_SCOPES,
    ScopeProfile,
    ScopeRefusal,
    active_profile,
    drafting_available,
    evaluate_granted_scopes,
    optional_scopes,
    permitted_scopes,
    requested_scope_string,
    require_acceptable_scopes,
)

READ_ONLY_GRANT = f"{GMAIL_READONLY} {OPENID} {EMAIL}"
DRAFT_GRANT = f"{READ_ONLY_GRANT} {GMAIL_COMPOSE}"
SEND = "https://www.googleapis.com/auth/gmail.send"


def grant_for(profile: ScopeProfile) -> str:
    """Everything the profile permits: the widest grant it can accept.

    Used so a parametrized case tests the scope it names, rather than tripping
    over compose being excess under the read-only profile.
    """
    return " ".join(sorted(permitted_scopes(profile)))


@pytest.fixture
def drafting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")


@pytest.fixture(autouse=True)
def _no_inherited_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's shell must not decide what these tests assert."""
    monkeypatch.delenv(PROFILE_VARIABLE, raising=False)


# ===========================================================================
# WHICH PROFILE IS ACTIVE
# ===========================================================================


def test_the_default_is_read_only() -> None:
    assert active_profile() is ScopeProfile.READ_ONLY
    assert optional_scopes() == frozenset()
    assert permitted_scopes() == REQUIRED_SCOPES


@pytest.mark.parametrize(
    "value",
    ["", "   ", "nonsense", "read_write", "gmail.send", "true", "1", "read-and-draft", "None"],
)
def test_an_unreadable_setting_falls_back_to_read_only(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A typo must narrow access, never widen it."""
    monkeypatch.setenv(PROFILE_VARIABLE, value)
    assert active_profile() is ScopeProfile.READ_ONLY
    assert GMAIL_COMPOSE not in requested_scope_string()


@pytest.mark.parametrize("value", ["read_and_draft", "READ_AND_DRAFT", "  Read_And_Draft  "])
def test_the_drafting_profile_is_selected_case_insensitively(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(PROFILE_VARIABLE, value)
    assert active_profile() is ScopeProfile.READ_AND_DRAFT


def test_every_profile_declares_its_optional_scopes() -> None:
    for profile in ScopeProfile:
        assert isinstance(optional_scopes(profile), frozenset)
        assert REQUIRED_SCOPES <= permitted_scopes(profile)


# ===========================================================================
# WHAT CONSENT ASKS FOR
# ===========================================================================


def test_the_read_only_request_is_exactly_what_it_was() -> None:
    assert requested_scope_string() == " ".join(sorted({GMAIL_READONLY, OPENID, EMAIL}))
    assert GMAIL_COMPOSE not in requested_scope_string()


def test_the_drafting_request_adds_compose_and_nothing_else(drafting: None) -> None:
    requested = requested_scope_string().split()

    assert set(requested) == {GMAIL_READONLY, OPENID, EMAIL, GMAIL_COMPOSE}
    assert requested == sorted(requested), "a stable order keeps the consent screen reviewable"
    assert len(requested) == len(set(requested))


@pytest.mark.parametrize("profile", list(ScopeProfile))
def test_no_profile_ever_asks_for_a_send_capable_scope(profile: ScopeProfile) -> None:
    requested = set(requested_scope_string(profile).split())
    assert not (requested & NEVER_PERMITTED_SCOPES)
    assert SEND not in requested


def test_the_consent_url_carries_the_profile_scopes(
    monkeypatch: pytest.MonkeyPatch, drafting: None
) -> None:
    from urllib.parse import parse_qs, urlparse

    from app.mailbox.google import OAuthClientConfig, build_authorization_url

    url = build_authorization_url(
        OAuthClientConfig("id.apps.googleusercontent.com", "secret", "https://app/cb"),
        state="state-value",
        code_challenge="challenge",
        login_hint="support@acme.example",
    )
    query = parse_qs(urlparse(url).query)

    assert GMAIL_COMPOSE in query["scope"][0].split()
    # Still never merging previously granted scopes back in: that is how a
    # token acquires gmail.send without anyone asking.
    assert query["include_granted_scopes"] == ["false"]
    assert query["prompt"] == ["consent select_account"]


def test_the_read_only_consent_url_does_not_mention_compose() -> None:
    from app.mailbox.google import OAuthClientConfig, build_authorization_url

    url = build_authorization_url(
        OAuthClientConfig("id.apps.googleusercontent.com", "secret", "https://app/cb"),
        state="s",
        code_challenge="c",
        login_hint="support@acme.example",
    )
    assert "gmail.compose" not in url


# ===========================================================================
# WHAT A GRANT MAY CARRY
# ===========================================================================


def test_a_compose_grant_is_accepted_under_the_drafting_profile(drafting: None) -> None:
    decision = evaluate_granted_scopes(DRAFT_GRANT)

    assert decision.acceptable
    assert decision.reason_code is None
    assert decision.drafting
    assert decision.optional_missing == frozenset()
    assert decision.profile is ScopeProfile.READ_AND_DRAFT
    assert require_acceptable_scopes(DRAFT_GRANT) == frozenset(DRAFT_GRANT.split())


def test_a_compose_grant_is_still_refused_under_the_read_only_profile() -> None:
    decision = evaluate_granted_scopes(DRAFT_GRANT)

    assert not decision.acceptable
    assert decision.reason_code == "WRITE_SCOPE_GRANTED"
    with pytest.raises(ScopeRefusal) as refusal:
        require_acceptable_scopes(DRAFT_GRANT)
    assert refusal.value.excess == frozenset({GMAIL_COMPOSE})


def test_unticking_compose_connects_the_mailbox_read_only(drafting: None) -> None:
    """Granular consent: the flow succeeding does not mean compose was granted."""
    decision = evaluate_granted_scopes(READ_ONLY_GRANT)

    assert decision.acceptable, "a missing optional scope is not a refusal"
    assert decision.reason_code is None
    assert not decision.drafting
    assert decision.optional_missing == frozenset({GMAIL_COMPOSE})


@pytest.mark.parametrize("profile", list(ScopeProfile))
@pytest.mark.parametrize("scope", sorted(NEVER_PERMITTED_SCOPES))
def test_a_send_capable_scope_is_refused_under_every_profile(
    monkeypatch: pytest.MonkeyPatch, profile: ScopeProfile, scope: str
) -> None:
    monkeypatch.setenv(PROFILE_VARIABLE, profile.value)
    decision = evaluate_granted_scopes(f"{grant_for(profile)} {scope}")

    assert not decision.acceptable, f"{scope} was accepted under {profile.value}"
    assert decision.reason_code == "WRITE_SCOPE_GRANTED"
    assert scope in decision.excess


@pytest.mark.parametrize("profile", list(ScopeProfile))
def test_an_unrelated_scope_is_refused_under_every_profile(
    monkeypatch: pytest.MonkeyPatch, profile: ScopeProfile
) -> None:
    monkeypatch.setenv(PROFILE_VARIABLE, profile.value)
    decision = evaluate_granted_scopes(
        f"{grant_for(profile)} https://www.googleapis.com/auth/drive.readonly"
    )
    assert decision.reason_code == "UNEXPECTED_SCOPE_GRANTED"


@pytest.mark.parametrize("profile", list(ScopeProfile))
def test_the_mailbox_scope_is_required_under_every_profile(
    monkeypatch: pytest.MonkeyPatch, profile: ScopeProfile
) -> None:
    """Compose alone is not a connection: the product reads before it drafts."""
    monkeypatch.setenv(PROFILE_VARIABLE, profile.value)
    without_mailbox = " ".join(sorted(permitted_scopes(profile) - {GMAIL_READONLY}))
    decision = evaluate_granted_scopes(without_mailbox)

    assert not decision.acceptable
    assert decision.reason_code == "MAILBOX_SCOPE_DENIED"
    assert GMAIL_READONLY in decision.missing


def test_the_policy_and_the_never_permitted_list_agree() -> None:
    assert NEVER_PERMITTED_SCOPES == WRITE_CAPABLE_SCOPES - {GMAIL_COMPOSE}
    assert GMAIL_COMPOSE not in NEVER_PERMITTED_SCOPES
    assert SEND in NEVER_PERMITTED_SCOPES
    assert "https://mail.google.com/" in NEVER_PERMITTED_SCOPES
    # Nothing a profile permits may be on the refused list.
    for profile in ScopeProfile:
        assert not (permitted_scopes(profile) & NEVER_PERMITTED_SCOPES)


@pytest.mark.parametrize(
    ("granted", "expected"),
    [
        ([], False),
        ([GMAIL_READONLY], False),
        ([GMAIL_COMPOSE], True),
        ([GMAIL_READONLY, GMAIL_COMPOSE], True),
        (["gmail.compose"], False),
    ],
)
def test_drafting_available_asks_for_the_one_scope(granted: list[str], expected: bool) -> None:
    assert drafting_available(granted) is expected


@pytest.mark.parametrize(
    ("granted", "expected"),
    [
        (DRAFT_GRANT, True),
        (READ_ONLY_GRANT, False),
        (GMAIL_COMPOSE, True),
        ("", False),
        (f"{GMAIL_READONLY} openid email", False),
    ],
)
def test_drafting_available_accepts_googles_own_scope_string(granted: str, expected: bool) -> None:
    """The space-delimited shape, not only a list: a string of one scope is one
    scope, never a sequence of characters."""
    assert drafting_available(granted) is expected


def test_drafting_available_is_the_one_definition_the_composer_uses() -> None:
    from app.review.drafts import can_create_drafts

    for granted in ([], [GMAIL_READONLY], [GMAIL_COMPOSE], [GMAIL_READONLY, GMAIL_COMPOSE]):
        assert can_create_drafts(granted) == drafting_available(granted)
