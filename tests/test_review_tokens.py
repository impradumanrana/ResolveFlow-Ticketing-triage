"""Access tokens for creating a draft, and the wiring that supplies them.

Two things are being held here. First, that the token provider never becomes a
place a credential lives: it holds one short-lived access token in memory, does
not re-decide C06's refusals, and cannot hand on an empty bearer. Second, that
the review service is given a transport and a token provider *only* under the
drafting profile with the OAuth client configured - so a deployment that was
never authorized to write keeps refusing, without anyone remembering to turn
anything off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.mailbox.scopes import PROFILE_VARIABLE
from app.review.tokens import ACCESS_TOKEN_TTL, RefreshingTokenProvider, TokenUnavailable

MAILBOX = "mbx-support"
OTHER = "mbx-sales"


@dataclass(frozen=True)
class Outcome:
    """The shape C06's `ConnectionService.refresh` returns."""

    ok: bool
    code: str
    access_token: str | None = None


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


class FakeRefresh:
    def __init__(self, *outcomes: Outcome) -> None:
        self.queue = list(outcomes)
        self.calls: list[str] = []
        self.counter = 0

    def __call__(self, mailbox_id: str) -> Outcome:
        self.calls.append(mailbox_id)
        if self.queue:
            return self.queue.pop(0)
        self.counter += 1
        return Outcome(True, "REFRESHED", f"ya29.token-{self.counter}")


@pytest.fixture
def clock() -> Clock:
    return Clock()


# ===========================================================================
# ASKING C06
# ===========================================================================


def test_a_token_is_returned_and_then_reused(clock: Clock) -> None:
    refresh = FakeRefresh()
    tokens = RefreshingTokenProvider(refresh, now=clock)

    first = tokens(MAILBOX)

    assert first == "ya29.token-1"
    assert tokens(MAILBOX) == first
    assert refresh.calls == [MAILBOX], "the second call went to Google"


def test_each_mailbox_is_cached_separately(clock: Clock) -> None:
    tokens = RefreshingTokenProvider(FakeRefresh(), now=clock)

    assert tokens(MAILBOX) != tokens(OTHER)
    assert tokens(MAILBOX) == "ya29.token-1"


def test_the_token_is_refreshed_once_the_window_closes(clock: Clock) -> None:
    refresh = FakeRefresh()
    tokens = RefreshingTokenProvider(refresh, now=clock)

    assert tokens(MAILBOX) == "ya29.token-1"
    clock.advance(minutes=ACCESS_TOKEN_TTL.total_seconds() / 60 - 1)
    assert tokens(MAILBOX) == "ya29.token-1", "refreshed early"

    clock.advance(minutes=2)
    assert tokens(MAILBOX) == "ya29.token-2"
    assert len(refresh.calls) == 2


def test_the_window_is_shorter_than_googles_hour() -> None:
    """A token that expires mid-call fails a draft a person is watching."""
    assert timedelta(minutes=30) <= ACCESS_TOKEN_TTL < timedelta(hours=1)


# ===========================================================================
# FAILURE
# ===========================================================================


@pytest.mark.parametrize(
    "code",
    [
        "MAILBOX_NOT_FOUND",
        "MAILBOX_REVOKED",
        "NOT_CONNECTED",
        "CREDENTIAL_UNREADABLE",
        "INVALID_GRANT",
        "SCOPE_REMOVED",
        "WRITE_SCOPE_GRANTED",
    ],
)
def test_c06s_refusal_is_passed_on_rather_than_re_decided(clock: Clock, code: str) -> None:
    tokens = RefreshingTokenProvider(FakeRefresh(Outcome(False, code)), now=clock)

    with pytest.raises(TokenUnavailable) as raised:
        tokens(MAILBOX)

    assert raised.value.code == code


@pytest.mark.parametrize("token", [None, "", "   " and ""])
def test_a_success_with_no_token_is_treated_as_a_failure(clock: Clock, token: str | None) -> None:
    """Would be a C06 bug. Never handed on as an empty bearer."""
    tokens = RefreshingTokenProvider(FakeRefresh(Outcome(True, "REFRESHED", token)), now=clock)

    with pytest.raises(TokenUnavailable) as raised:
        tokens(MAILBOX)

    assert raised.value.code == "MAILBOX_TOKEN_MISSING"


def test_a_failure_is_not_cached(clock: Clock) -> None:
    refresh = FakeRefresh(Outcome(False, "INVALID_GRANT"))
    tokens = RefreshingTokenProvider(refresh, now=clock)

    with pytest.raises(TokenUnavailable):
        tokens(MAILBOX)
    assert tokens(MAILBOX) == "ya29.token-1", "a failure poisoned the mailbox"


def test_a_failed_refresh_clears_a_token_that_was_already_stale(clock: Clock) -> None:
    """The expired token must not survive a failure and be served later."""
    refresh = FakeRefresh(Outcome(True, "REFRESHED", "ya29.first"), Outcome(False, "INVALID_GRANT"))
    tokens = RefreshingTokenProvider(refresh, now=clock)

    assert tokens(MAILBOX) == "ya29.first"
    clock.advance(hours=2)
    with pytest.raises(TokenUnavailable):
        tokens(MAILBOX)

    # Back in time, so a stale entry left behind would now look valid again.
    clock.advance(hours=-2)
    assert tokens(MAILBOX) == "ya29.token-1"


def test_invalidate_forces_the_next_call_to_refresh(clock: Clock) -> None:
    refresh = FakeRefresh()
    tokens = RefreshingTokenProvider(refresh, now=clock)

    assert tokens(MAILBOX) == "ya29.token-1"
    tokens.invalidate(MAILBOX)

    assert tokens(MAILBOX) == "ya29.token-2"
    tokens.invalidate("never-cached")  # must not raise


def test_an_unknown_outcome_shape_fails_closed(clock: Clock) -> None:
    tokens = RefreshingTokenProvider(lambda mailbox_id: object(), now=clock)

    with pytest.raises(TokenUnavailable) as raised:
        tokens(MAILBOX)

    assert raised.value.code == "MAILBOX_TOKEN_UNAVAILABLE"


# ===========================================================================
# THE TOKEN IS NOT A PLACE A CREDENTIAL LIVES (C-D009)
# ===========================================================================


def test_a_cached_token_is_not_printable(clock: Clock) -> None:
    from app.review.tokens import _CachedToken

    cached = _CachedToken(access_token="ya29.secret-value", expires_at=clock())

    assert "ya29.secret-value" not in repr(cached)
    assert "ya29.secret-value" not in str(cached)


def test_the_module_never_touches_a_refresh_token() -> None:
    """The sealed credential is C06's. This module only ever sees an access token."""
    import pathlib

    source = pathlib.Path("app/review/tokens.py").read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("#", "*"))
    )
    code = code.split('"""', 2)[-1]

    for forbidden in ("refresh_token", "vault", "open(", "envelope", "client_secret"):
        assert forbidden not in code, f"{forbidden} appears in the token provider"


# ===========================================================================
# WIRING: A TRANSPORT ONLY WHERE WRITING WAS AUTHORIZED
# ===========================================================================

OAUTH_ENVIRONMENT = {
    "GMAIL_OAUTH_CLIENT_ID": "id.apps.googleusercontent.com",
    "GMAIL_OAUTH_CLIENT_SECRET": "not-a-real-secret",
    "GMAIL_OAUTH_REDIRECT_URI": "https://support.acme.example/api/mailboxes/oauth/callback",
}


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch):
    """`_drafting_dependencies` is cached per process; clear it around each case."""
    from app.api import review as api

    api._drafting_dependencies.cache_clear()
    monkeypatch.delenv(PROFILE_VARIABLE, raising=False)
    for name in OAUTH_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    # A throwaway key, not a credential: nothing is sealed with it here.
    monkeypatch.setenv(
        "MAILBOX_TOKEN_ENCRYPTION_KEY", "key-2026-09:MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
    )
    yield api
    api._drafting_dependencies.cache_clear()


def _configure_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in OAUTH_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


def test_the_read_only_deployment_gets_no_transport(
    wiring, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_oauth(monkeypatch)
    assert wiring._drafting_dependencies("postgresql://unused") is None


def test_the_drafting_profile_without_an_oauth_client_gets_no_transport(
    wiring, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")
    assert wiring._drafting_dependencies("postgresql://unused") is None


@pytest.mark.parametrize("missing", sorted(OAUTH_ENVIRONMENT))
def test_a_partly_configured_oauth_client_gets_no_transport(
    wiring, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")
    _configure_oauth(monkeypatch)
    monkeypatch.setenv(missing, "")
    assert wiring._drafting_dependencies("postgresql://unused") is None


def test_an_unopenable_vault_gets_no_transport(wiring, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing key is a deployment that cannot read its own credentials."""
    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")
    _configure_oauth(monkeypatch)
    monkeypatch.setenv("MAILBOX_TOKEN_ENCRYPTION_KEY", "")
    assert wiring._drafting_dependencies("postgresql://unused") is None


def test_the_authorized_deployment_gets_a_transport_and_a_token_provider(
    wiring, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")
    _configure_oauth(monkeypatch)
    # No database here: this asserts what gets wired, not what it queries.
    monkeypatch.setattr(wiring, "_engine", lambda url: object())

    dependencies = wiring._drafting_dependencies("postgresql://unused")

    assert dependencies is not None
    transport, tokens = dependencies
    assert hasattr(transport, "request")
    assert isinstance(tokens, RefreshingTokenProvider)


# ===========================================================================
# THE WHOLE CHAIN
#
# Everything above tests one piece. This runs consent, credential storage,
# token refresh, and draft creation together, with nothing stubbed but Google
# itself - because each piece agreeing in isolation is what let the earlier
# duck-typed address bug through.
# ===========================================================================


class Unreachable:
    """A transport that fails the test if Gmail is called at all."""

    def __init__(self) -> None:
        self.requests: list[object] = []

    def request(self, *args, **kwargs):
        self.requests.append(args)
        raise AssertionError("Gmail was called when it should not have been")


def connected_mailbox(granted_scope: str):
    """Consent, through C06's simulated Google, and return (env, credential)."""
    from test_mailbox_connection import ADMIN as MAILBOX_ADMIN
    from test_mailbox_connection import SUPPORT, Environment

    env = Environment()
    env.google.granted_scope = granted_scope
    outcome = env.connect(MAILBOX_ADMIN)
    assert outcome.ok, outcome.code
    credential = env.store.get_credential(SUPPORT)
    assert credential is not None
    return env, credential


def review_for(granted_scopes, *, transport, token_provider):
    """A review service over the in-memory store, wired as the API wires it."""
    import uuid

    from test_mailbox_connection import SUPPORT

    from app.review.memory_store import InMemoryReviewStore
    from app.review.records import Actor, Decision, ReviewRequest, TicketFacts
    from app.review.service import ReviewService

    org, ticket_id, thread, membership = "org-acme", str(uuid.uuid4()), "thread-1", "m-admin"
    facts = TicketFacts(
        ticket_id=ticket_id,
        organization_id=org,
        mailbox_id=SUPPORT,
        thread_id=thread,
        version=1,
        status="OPEN",
        mailbox_address="support@acme.example",
        customer_address="customer@example.net",
        subject="Reset my password",
        model_draft="Here is how to reset it.",
        citations=("kb-1",),
        grounding_validated=True,
        granted_scopes=tuple(granted_scopes),
    )
    store = InMemoryReviewStore(
        tickets={ticket_id: facts},
        action_permissions={membership: {SUPPORT}},
        inbound={
            thread: {
                "from_address": "Customer <customer@example.net>",
                "subject": "Reset my password",
                "rfc822_message_id": "<first@mail>",
                "to_addresses": ["support@acme.example"],
                "cc_addresses": [],
            }
        },
        mailbox_addresses={SUPPORT: "support@acme.example"},
    )
    service = ReviewService(store, transport=transport, token_provider=token_provider)
    request = ReviewRequest(
        organization_id=org,
        ticket_id=ticket_id,
        actor=Actor(membership_id=membership, organization_id=org, role="ADMIN"),
        decision=Decision.APPROVE,
        idempotency_key=f"key-{uuid.uuid4()}",
        expected_version=1,
    )
    return service, store, request


def test_an_approval_becomes_a_gmail_draft_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    import base64

    from test_mailbox_connection import SUPPORT

    from app.mailbox.google import HttpResponse
    from app.mailbox.scopes import EMAIL, GMAIL_COMPOSE, GMAIL_READONLY, OPENID
    from app.review.drafts import DRAFTS_ENDPOINT

    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")

    # 1. A Workspace admin consents, compose included.
    env, credential = connected_mailbox(f"{GMAIL_READONLY} {OPENID} {EMAIL} {GMAIL_COMPOSE}")

    # 2. Gmail's drafts endpoint, which answers only for a live access token.
    class FakeGmail:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def request(self, method, url, *, json=None, bearer=None, timeout=20.0):
            self.requests.append({"method": method, "url": url, "bearer": bearer, "json": json})
            assert "send" not in url, "the only endpoint is the drafts endpoint"
            if bearer not in env.google.access_tokens:
                return HttpResponse(401, {"error": {"message": "Invalid Credentials"}})
            message = (json or {}).get("message") or {}
            assert "raw" in message, "the draft carries a MIME message, nothing else"
            return HttpResponse(
                200, {"id": "r-draft-1", "message": {"threadId": message.get("threadId")}}
            )

    gmail = FakeGmail()
    tokens = RefreshingTokenProvider(env.service.refresh)

    # 3. The approval.
    service, store, request = review_for(
        credential.granted_scopes, transport=gmail, token_provider=tokens
    )
    outcome = service.review(request)

    # 4. A draft exists in the mailbox, and only a draft.
    assert outcome.ok, outcome.code
    assert outcome.provider_draft_created
    assert outcome.draft is not None and outcome.draft.provider_draft_id == "r-draft-1"
    assert store.drafts[0]["status"] == "CREATED"

    assert len(gmail.requests) == 1
    call = gmail.requests[0]
    assert call["method"] == "POST"
    assert call["url"] == DRAFTS_ENDPOINT
    assert call["bearer"] in env.google.access_tokens

    raw = str((call["json"] or {})["message"]["raw"])  # type: ignore[index]
    mime = base64.urlsafe_b64decode(raw).decode()
    assert "To: customer@example.net" in mime
    assert "Subject: Re: Reset my password" in mime
    assert "Here is how to reset it." in mime

    # 5. One refresh, reused: a second draft does not re-authenticate.
    before = len([c for c in env.google.calls if c[1].endswith("/token")])
    assert tokens(SUPPORT) == call["bearer"]
    assert len([c for c in env.google.calls if c[1].endswith("/token")]) == before


def test_the_chain_refuses_when_compose_was_unticked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same wiring, a mailbox connected read-only: approval, no draft."""
    from app.mailbox.scopes import EMAIL, GMAIL_READONLY, OPENID

    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")
    env, credential = connected_mailbox(f"{GMAIL_READONLY} {OPENID} {EMAIL}")
    gmail = Unreachable()

    service, _, request = review_for(
        credential.granted_scopes,
        transport=gmail,
        token_provider=RefreshingTokenProvider(env.service.refresh),
    )
    outcome = service.review(request)

    assert outcome.ok, "the decision stands; only the draft was refused"
    assert not outcome.provider_draft_created
    assert outcome.draft is not None
    assert outcome.draft.failure_code == "DRAFT_SCOPE_NOT_GRANTED"
    assert gmail.requests == []


def test_a_transport_with_no_token_provider_does_not_call_gmail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise an unauthenticated 401 would be reported as the mailbox's fault."""
    from app.mailbox.scopes import EMAIL, GMAIL_COMPOSE, GMAIL_READONLY, OPENID

    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")
    _, credential = connected_mailbox(f"{GMAIL_READONLY} {OPENID} {EMAIL} {GMAIL_COMPOSE}")
    gmail = Unreachable()

    service, store, request = review_for(
        credential.granted_scopes, transport=gmail, token_provider=None
    )
    outcome = service.review(request)

    assert outcome.ok, "the decision stands"
    assert not outcome.provider_draft_created
    assert outcome.draft is not None
    assert outcome.draft.status == "FAILED"
    assert outcome.draft.failure_code == "MAILBOX_TOKEN_UNAVAILABLE"
    assert gmail.requests == [], "Gmail was called with no bearer"
    assert store.drafts[0]["status"] == "FAILED"


def test_a_token_refusal_leaves_the_approval_standing(monkeypatch: pytest.MonkeyPatch) -> None:
    """C06 refusing a token is reported as a draft failure, not a lost decision."""
    from app.mailbox.scopes import EMAIL, GMAIL_COMPOSE, GMAIL_READONLY, OPENID

    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")
    _, credential = connected_mailbox(f"{GMAIL_READONLY} {OPENID} {EMAIL} {GMAIL_COMPOSE}")
    gmail = Unreachable()
    tokens = RefreshingTokenProvider(FakeRefresh(Outcome(False, "MAILBOX_REVOKED")))

    service, _, request = review_for(
        credential.granted_scopes, transport=gmail, token_provider=tokens
    )
    outcome = service.review(request)

    assert outcome.ok
    assert outcome.draft is not None
    assert outcome.draft.failure_code == "MAILBOX_TOKEN_UNAVAILABLE"
    assert gmail.requests == []


def test_an_empty_token_is_never_passed_to_gmail(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider that returns nothing is a bug, not an anonymous request."""
    from app.mailbox.scopes import EMAIL, GMAIL_COMPOSE, GMAIL_READONLY, OPENID

    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")
    _, credential = connected_mailbox(f"{GMAIL_READONLY} {OPENID} {EMAIL} {GMAIL_COMPOSE}")
    gmail = Unreachable()

    service, _, request = review_for(
        credential.granted_scopes, transport=gmail, token_provider=lambda mailbox_id: ""
    )
    outcome = service.review(request)

    assert outcome.ok
    assert outcome.draft is not None
    assert outcome.draft.status == "FAILED"
    assert outcome.draft.failure_code == "MAILBOX_TOKEN_UNAVAILABLE"
    assert gmail.requests == []
