"""C06 gate: connect, refresh, revoke, reconnect, scope denial, wrong mailbox.

Every case runs against a simulated Google. No real account, network call, or
paid service is involved. The simulation is strict where Google is strict - it
verifies the PKCE verifier against the challenge from the authorization URL,
refuses revoked tokens, and only answers the profile call for live access
tokens - so a service bug that real Google would reject is rejected here too.
"""

from __future__ import annotations

import inspect
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.mailbox.access import Actor
from app.mailbox.connection import (
    ATTEMPT_LIFETIME,
    ConnectionRefused,
    ConnectionService,
    credential_reference,
)
from app.mailbox.google import (
    GMAIL_PROFILE_ENDPOINT,
    REVOKE_ENDPOINT,
    TOKEN_ENDPOINT,
    GoogleOAuthClient,
    HttpResponse,
    OAuthClientConfig,
    ProviderError,
    pkce_challenge,
)
from app.mailbox.memory_store import InMemoryMailboxStore
from app.mailbox.records import MailboxRecord
from app.mailbox.scopes import EMAIL, GMAIL_COMPOSE, GMAIL_READONLY, OPENID, PROFILE_VARIABLE
from app.mailbox.vault import TokenVault

ORG = "org-acme"
OTHER_ORG = "org-other"
SUPPORT = "mbx-support"
SALES = "mbx-sales"
GROUP = "mbx-group"
ALIAS = "mbx-alias"
OFF_DOMAIN = "mbx-offdomain"
FOREIGN = "mbx-foreign"

READ_ONLY_GRANT = f"{GMAIL_READONLY} {OPENID} {EMAIL}"
DRAFT_GRANT = f"{READ_ONLY_GRANT} {GMAIL_COMPOSE}"
SEND = "https://www.googleapis.com/auth/gmail.send"


class FakeGoogle:
    """A strict stand-in for Google's token, revoke, and Gmail profile endpoints."""

    def __init__(self) -> None:
        self.granted_scope = READ_ONLY_GRANT
        self.account_email = "support@acme.example"
        self.issue_refresh_token = True
        self.refresh_mode = "ok"  # ok | invalid_grant | timeout | reduced_scope
        self.revoke_fails = False
        self.calls: list[tuple[str, str]] = []
        self._codes: dict[str, str] = {}
        self._counter = 0
        self.refresh_tokens: set[str] = set()
        self.access_tokens: set[str] = set()
        self.revoked: set[str] = set()

    def consent(self, authorization_url: str) -> tuple[str, str]:
        """Simulate a person approving the consent screen. Returns (state, code)."""
        query = parse_qs(urlparse(authorization_url).query)
        self._counter += 1
        code = f"code-{self._counter}"
        self._codes[code] = query["code_challenge"][0]
        return query["state"][0], code

    def _mint(self) -> tuple[str, str]:
        self._counter += 1
        access = f"ya29.fake-access-{self._counter}"
        refresh = f"1//0fake-refresh-{self._counter}"
        self.access_tokens.add(access)
        return access, refresh

    def request(self, method, url, *, form=None, bearer=None, timeout=15.0):
        self.calls.append((method, url))
        form = form or {}

        if url == TOKEN_ENDPOINT and form.get("grant_type") == "authorization_code":
            challenge = self._codes.pop(form.get("code", ""), None)
            if challenge is None or pkce_challenge(form.get("code_verifier", "")) != challenge:
                return HttpResponse(400, {"error": "invalid_grant"})
            access, refresh = self._mint()
            body = {"access_token": access, "expires_in": 3599, "scope": self.granted_scope}
            if self.issue_refresh_token:
                self.refresh_tokens.add(refresh)
                body["refresh_token"] = refresh
            return HttpResponse(200, body)

        if url == TOKEN_ENDPOINT and form.get("grant_type") == "refresh_token":
            token = form.get("refresh_token", "")
            if self.refresh_mode == "timeout":
                raise ProviderError("PROVIDER_TIMEOUT", transient=True)
            if (
                self.refresh_mode == "invalid_grant"
                or token in self.revoked
                or token not in self.refresh_tokens
            ):
                return HttpResponse(400, {"error": "invalid_grant"})
            access, _ = self._mint()
            scope = (
                f"{OPENID} {EMAIL}" if self.refresh_mode == "reduced_scope" else self.granted_scope
            )
            return HttpResponse(200, {"access_token": access, "expires_in": 3599, "scope": scope})

        if url == REVOKE_ENDPOINT:
            if self.revoke_fails:
                return HttpResponse(503, {})
            self.revoked.add(form.get("token", ""))
            return HttpResponse(200, {})

        if url == GMAIL_PROFILE_ENDPOINT:
            if bearer not in self.access_tokens or bearer in self.revoked:
                return HttpResponse(401, {"error": {"code": 401}})
            return HttpResponse(200, {"emailAddress": self.account_email, "messagesTotal": 12})

        return HttpResponse(404, {})

    def calls_to(self, url: str) -> int:
        return sum(1 for _, called in self.calls if called == url)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class Environment:
    def __init__(self) -> None:
        self.google = FakeGoogle()
        self.clock = Clock()
        self.store = InMemoryMailboxStore(
            mailboxes=[
                MailboxRecord(SUPPORT, ORG, "support@acme.example", "SHARED_MAILBOX", "PENDING"),
                MailboxRecord(SALES, ORG, "sales@acme.example", "SHARED_MAILBOX", "PENDING"),
                MailboxRecord(GROUP, ORG, "team@acme.example", "GROUP", "PENDING"),
                MailboxRecord(ALIAS, ORG, "help@acme.example", "ALIAS", "PENDING"),
                MailboxRecord(
                    OFF_DOMAIN, ORG, "support@elsewhere.example", "SHARED_MAILBOX", "PENDING"
                ),
                MailboxRecord(
                    FOREIGN, OTHER_ORG, "support@other.example", "SHARED_MAILBOX", "PENDING"
                ),
            ],
            allowed_domains={
                ORG: frozenset({"acme.example"}),
                OTHER_ORG: frozenset({"other.example"}),
            },
        )
        self.vault = TokenVault({"key-2026-09": os.urandom(32)}, active_key_id="key-2026-09")
        self.oauth = GoogleOAuthClient(
            OAuthClientConfig(
                client_id="client-id.apps.googleusercontent.com",
                client_secret="not-a-real-secret",
                redirect_uri="https://support.acme.example/api/mailboxes/oauth/callback",
            ),
            transport=self.google,
        )
        self.service = ConnectionService(self.store, self.oauth, self.vault, now=self.clock)

    def connect(self, actor: Actor, mailbox_id: str = SUPPORT):
        started = self.service.start(actor, mailbox_id)
        state, code = self.google.consent(started.authorization_url)
        return self.service.complete(actor, state=state, code=code)

    def status(self, mailbox_id: str = SUPPORT) -> str:
        return self.store.mailboxes[mailbox_id].status

    def audit_actions(self) -> list[str]:
        return [event["action"] for event in self.store.audit]


def member(
    role: str, membership_id: str | None = None, org: str = ORG, status: str = "ACTIVE"
) -> Actor:
    return Actor(membership_id or f"m-{role.lower()}", org, role, status)


ADMIN = member("ADMIN")
OWNER = member("OWNER")


@pytest.fixture
def env() -> Environment:
    return Environment()


# ===========================================================================
# CONNECT
# ===========================================================================


def test_an_admin_connects_a_shared_mailbox(env: Environment) -> None:
    outcome = env.connect(ADMIN)

    assert outcome.ok and outcome.code == "CONNECTED" and outcome.mailbox_id == SUPPORT
    assert env.status() == "CONNECTED"
    assert env.store.mailboxes[SUPPORT].revoked_at is None
    assert "mailbox.connected" in env.audit_actions()


def test_the_stored_credential_is_sealed_and_names_its_key(env: Environment) -> None:
    env.connect(ADMIN)
    credential = env.store.get_credential(SUPPORT)
    issued = next(iter(env.google.refresh_tokens))

    assert credential is not None
    assert issued not in credential.sealed.envelope
    assert credential.sealed.envelope.startswith("v1.")
    assert env.store.mailbox_fields[SUPPORT]["credential_reference"] == credential_reference(
        "key-2026-09"
    )
    assert credential.provider_account_email == "support@acme.example"


def test_no_access_token_is_ever_persisted(env: Environment) -> None:
    env.connect(ADMIN)
    stored = repr(env.store.credentials) + repr(env.store.mailbox_fields) + repr(env.store.audit)

    for token in env.google.access_tokens | env.google.refresh_tokens:
        assert token not in stored


def test_the_authorization_url_requests_offline_read_only_access(env: Environment) -> None:
    started = env.service.start(ADMIN, SUPPORT)
    query = parse_qs(urlparse(started.authorization_url).query)

    assert query["access_type"] == ["offline"]
    assert "consent" in query["prompt"][0]
    assert query["include_granted_scopes"] == ["false"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["login_hint"] == ["support@acme.example"]
    requested = set(query["scope"][0].split())
    assert GMAIL_READONLY in requested
    assert SEND not in requested
    assert "https://mail.google.com/" not in requested


def test_the_pkce_verifier_sent_matches_the_challenge_offered(env: Environment) -> None:
    """The fake refuses a mismatched verifier, so success proves they match."""
    assert env.connect(ADMIN).ok


def test_the_state_is_not_stored_in_recoverable_form(env: Environment) -> None:
    started = env.service.start(ADMIN, SUPPORT)
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
    attempt = env.store.attempts[started.attempt_id]

    assert state not in repr(attempt)
    assert len(attempt.state_hash) == 64


@pytest.mark.parametrize("role", ["SUPERVISOR", "AGENT", "KNOWLEDGE_MANAGER", "AUDITOR"])
def test_only_owner_and_admin_may_start_a_connection(env: Environment, role: str) -> None:
    with pytest.raises(ConnectionRefused) as refused:
        env.service.start(member(role), SUPPORT)

    assert refused.value.code == "ROLE_CANNOT_ADMINISTER_MAILBOXES"
    assert env.store.attempts == {}


def test_an_owner_may_connect(env: Environment) -> None:
    assert env.connect(OWNER).ok


def test_an_inactive_membership_cannot_start(env: Environment) -> None:
    with pytest.raises(ConnectionRefused) as refused:
        env.service.start(member("ADMIN", status="SUSPENDED"), SUPPORT)
    assert refused.value.code == "MEMBERSHIP_NOT_ACTIVE"


@pytest.mark.parametrize("mailbox_id", [GROUP, ALIAS])
def test_groups_and_aliases_are_not_connectable(env: Environment, mailbox_id: str) -> None:
    with pytest.raises(ConnectionRefused) as refused:
        env.service.start(ADMIN, mailbox_id)
    assert refused.value.code == "MAILBOX_KIND_NOT_CONNECTABLE"


def test_a_mailbox_outside_the_approved_domains_is_refused_before_oauth(env: Environment) -> None:
    with pytest.raises(ConnectionRefused) as refused:
        env.service.start(ADMIN, OFF_DOMAIN)

    assert refused.value.code == "DOMAIN_NOT_ALLOWED"
    assert env.google.calls == []


def test_another_organizations_mailbox_looks_like_it_does_not_exist(env: Environment) -> None:
    with pytest.raises(ConnectionRefused) as refused:
        env.service.start(ADMIN, FOREIGN)
    assert refused.value.code == "MAILBOX_NOT_FOUND"


def test_a_missing_or_unknown_state_is_refused(env: Environment) -> None:
    assert env.service.complete(ADMIN, state="", code="x").code == "STATE_MISSING"
    assert env.service.complete(ADMIN, state="made-up", code="x").code == "STATE_UNKNOWN"


def test_a_callback_cannot_be_replayed(env: Environment) -> None:
    started = env.service.start(ADMIN, SUPPORT)
    state, code = env.google.consent(started.authorization_url)

    first = env.service.complete(ADMIN, state=state, code=code)
    second = env.service.complete(ADMIN, state=state, code=code)

    assert first.ok
    assert second.code == "ATTEMPT_REPLAYED"
    assert env.google.calls_to(TOKEN_ENDPOINT) == 1


def test_an_expired_attempt_is_refused_and_consumed(env: Environment) -> None:
    started = env.service.start(ADMIN, SUPPORT)
    state, code = env.google.consent(started.authorization_url)
    env.clock.advance(ATTEMPT_LIFETIME + timedelta(seconds=1))

    assert env.service.complete(ADMIN, state=state, code=code).code == "ATTEMPT_EXPIRED"
    assert env.google.calls_to(TOKEN_ENDPOINT) == 0
    assert env.service.complete(ADMIN, state=state, code=code).code == "ATTEMPT_REPLAYED"


def test_a_used_state_replayed_after_expiry_is_still_reported_as_a_replay(
    env: Environment,
) -> None:
    """Expiry must not mask a replay.

    Regression: the service once checked expiry before consumption, so a state
    that had already connected a mailbox, presented again after ten minutes,
    was reported as a stale link with no audit event. A reused credential is the
    fact an operator needs to see.
    """
    started = env.service.start(ADMIN, SUPPORT)
    state, code = env.google.consent(started.authorization_url)
    assert env.service.complete(ADMIN, state=state, code=code).ok

    env.clock.advance(ATTEMPT_LIFETIME + timedelta(minutes=5))
    replay = env.service.complete(ADMIN, state=state, code=code)

    assert replay.code == "ATTEMPT_REPLAYED"
    assert env.google.calls_to(TOKEN_ENDPOINT) == 1
    refusals = [e for e in env.store.audit if e["reason_code"] == "ATTEMPT_REPLAYED"]
    assert len(refusals) == 1


def test_a_replay_is_audited(env: Environment) -> None:
    started = env.service.start(ADMIN, SUPPORT)
    state, code = env.google.consent(started.authorization_url)
    env.service.complete(ADMIN, state=state, code=code)
    env.service.complete(ADMIN, state=state, code=code)

    assert any(e["reason_code"] == "ATTEMPT_REPLAYED" for e in env.store.audit)


def test_a_stolen_state_cannot_be_completed_by_someone_else(env: Environment) -> None:
    started = env.service.start(ADMIN, SUPPORT)
    state, code = env.google.consent(started.authorization_url)
    attacker = member("ADMIN", membership_id="m-other-admin")

    refused = env.service.complete(attacker, state=state, code=code)

    assert refused.code == "ATTEMPT_NOT_YOURS"
    assert env.google.calls_to(TOKEN_ENDPOINT) == 0
    # The legitimate person's attempt was not burned.
    assert env.service.complete(ADMIN, state=state, code=code).ok


def test_a_state_from_another_organization_is_refused(env: Environment) -> None:
    started = env.service.start(ADMIN, SUPPORT)
    state, code = env.google.consent(started.authorization_url)
    foreign = Actor(ADMIN.membership_id, OTHER_ORG, "ADMIN", "ACTIVE")

    assert env.service.complete(foreign, state=state, code=code).code == "ATTEMPT_NOT_YOURS"


def test_a_role_withdrawn_during_consent_stops_the_connection(env: Environment) -> None:
    started = env.service.start(ADMIN, SUPPORT)
    state, code = env.google.consent(started.authorization_url)
    demoted = replace(ADMIN, role="AGENT")

    outcome = env.service.complete(demoted, state=state, code=code)

    assert outcome.code == "ROLE_CANNOT_ADMINISTER_MAILBOXES"
    assert env.google.calls_to(TOKEN_ENDPOINT) == 0
    assert env.status() == "PENDING"


def test_a_grant_without_a_refresh_token_is_refused_and_revoked(env: Environment) -> None:
    env.google.issue_refresh_token = False

    outcome = env.connect(ADMIN)

    assert outcome.code == "NO_REFRESH_TOKEN"
    assert env.google.access_tokens <= env.google.revoked
    assert env.store.get_credential(SUPPORT) is None
    assert env.status() == "PENDING"


def test_a_rejected_authorization_code_stores_nothing(env: Environment) -> None:
    started = env.service.start(ADMIN, SUPPORT)
    state, _ = env.google.consent(started.authorization_url)

    outcome = env.service.complete(ADMIN, state=state, code="forged-code")

    assert outcome.code == "INVALID_GRANT"
    assert env.store.get_credential(SUPPORT) is None
    assert env.status() == "PENDING"


def test_the_callback_interface_cannot_name_a_mailbox() -> None:
    """Structural: the mailbox comes from the stored attempt, never the request."""
    parameters = set(inspect.signature(ConnectionService.complete).parameters)
    assert parameters == {"self", "actor", "state", "code"}


# ===========================================================================
# SCOPE DENIAL
# ===========================================================================


def test_denying_the_mailbox_scope_is_refused_and_the_grant_revoked(env: Environment) -> None:
    env.google.granted_scope = f"{OPENID} {EMAIL}"

    outcome = env.connect(ADMIN)

    assert outcome.code == "MAILBOX_SCOPE_DENIED"
    assert env.google.refresh_tokens <= env.google.revoked
    assert env.store.get_credential(SUPPORT) is None
    assert env.status() == "PENDING"
    refusal = next(e for e in env.store.audit if e["action"] == "mailbox.connection_failed")
    assert GMAIL_READONLY in refusal["metadata"]["missing"]


def test_denying_the_identity_scope_is_refused(env: Environment) -> None:
    env.google.granted_scope = f"{GMAIL_READONLY} {OPENID}"

    assert env.connect(ADMIN).code == "IDENTITY_SCOPE_DENIED"
    assert env.store.get_credential(SUPPORT) is None


def test_a_write_scope_is_refused_even_when_granted_willingly(env: Environment) -> None:
    env.google.granted_scope = f"{READ_ONLY_GRANT} {SEND}"

    outcome = env.connect(ADMIN)

    assert outcome.code == "WRITE_SCOPE_GRANTED"
    assert env.google.refresh_tokens <= env.google.revoked
    assert env.store.get_credential(SUPPORT) is None


def test_full_mail_access_is_refused(env: Environment) -> None:
    env.google.granted_scope = f"{READ_ONLY_GRANT} https://mail.google.com/"
    assert env.connect(ADMIN).code == "WRITE_SCOPE_GRANTED"


def test_an_unrelated_extra_scope_is_refused(env: Environment) -> None:
    env.google.granted_scope = f"{READ_ONLY_GRANT} https://www.googleapis.com/auth/drive.readonly"
    assert env.connect(ADMIN).code == "UNEXPECTED_SCOPE_GRANTED"


def test_google_short_scope_aliases_are_accepted(env: Environment) -> None:
    env.google.granted_scope = f"{GMAIL_READONLY} openid email"
    assert env.connect(ADMIN).ok


def test_a_revocation_failure_does_not_turn_a_refusal_into_success(env: Environment) -> None:
    env.google.granted_scope = f"{READ_ONLY_GRANT} {SEND}"
    env.google.revoke_fails = True

    outcome = env.connect(ADMIN)

    assert not outcome.ok
    assert outcome.code == "WRITE_SCOPE_GRANTED"
    assert env.store.get_credential(SUPPORT) is None


# ===========================================================================
# WRONG MAILBOX
# ===========================================================================


def test_consenting_with_a_personal_account_is_refused_and_revoked(env: Environment) -> None:
    env.google.account_email = "someone.personal@gmail.example"

    outcome = env.connect(ADMIN)

    assert outcome.code == "WRONG_MAILBOX_AUTHORIZED"
    assert env.google.refresh_tokens <= env.google.revoked
    assert env.store.get_credential(SUPPORT) is None
    assert env.status() == "PENDING"


def test_consenting_with_another_real_mailbox_in_the_same_organization_is_refused(
    env: Environment,
) -> None:
    """The subtle case: an approved domain, a real mailbox, just not this one."""
    env.google.account_email = "sales@acme.example"

    outcome = env.connect(ADMIN, SUPPORT)

    assert outcome.code == "WRONG_MAILBOX_AUTHORIZED"
    assert env.store.get_credential(SUPPORT) is None
    assert env.store.get_credential(SALES) is None
    assert env.status(SALES) == "PENDING"


def test_the_account_address_comparison_is_case_insensitive(env: Environment) -> None:
    env.google.account_email = "Support@ACME.example"
    assert env.connect(ADMIN).ok


def test_a_credential_moved_onto_another_mailbox_will_not_open(env: Environment) -> None:
    """Storage-layer wrong-mailbox: the ciphertext is bound to its mailbox."""
    env.connect(ADMIN, SUPPORT)
    stolen = env.store.get_credential(SUPPORT)
    assert stolen is not None

    env.store.save_credential(replace(stolen, mailbox_id=SALES))
    env.store.mailbox_fields[SALES]["credential_reference"] = "vault:copied"
    env.store.mailboxes[SALES] = replace(env.store.mailboxes[SALES], status="CONNECTED")
    calls_before = len(env.google.calls)

    outcome = env.service.refresh(SALES)

    assert outcome.code == "CREDENTIAL_UNREADABLE"
    assert outcome.access_token is None
    assert len(env.google.calls) == calls_before, "a mis-bound credential reached Google"
    assert env.status(SALES) == "DEGRADED"


def test_each_attempt_completes_only_the_mailbox_it_was_started_for(env: Environment) -> None:
    support = env.service.start(ADMIN, SUPPORT)
    env.google.account_email = "sales@acme.example"
    sales = env.service.start(ADMIN, SALES)

    sales_state, sales_code = env.google.consent(sales.authorization_url)
    outcome = env.service.complete(ADMIN, state=sales_state, code=sales_code)

    assert outcome.ok and outcome.mailbox_id == SALES
    assert env.status(SALES) == "CONNECTED"
    assert env.status(SUPPORT) == "PENDING"
    assert support.attempt_id in env.store.attempts


# ===========================================================================
# REFRESH
# ===========================================================================


def test_refresh_mints_an_access_token_without_storing_it(env: Environment) -> None:
    env.connect(ADMIN)

    outcome = env.service.refresh(SUPPORT)

    assert outcome.ok and outcome.code == "REFRESHED"
    assert outcome.access_token and outcome.access_token.startswith("ya29.")
    assert outcome.access_token not in repr(outcome)
    assert outcome.access_token not in repr(env.store.credentials)
    assert env.status() == "CONNECTED"


def test_a_grant_revoked_at_google_withdraws_the_mailbox(env: Environment) -> None:
    env.connect(ADMIN)
    env.google.refresh_mode = "invalid_grant"

    outcome = env.service.refresh(SUPPORT)

    assert outcome.code == "INVALID_GRANT"
    assert env.status() == "REVOKED"
    assert env.store.mailboxes[SUPPORT].revoked_at is not None
    assert env.store.get_credential(SUPPORT) is None
    assert "mailbox.access_withdrawn" in env.audit_actions()


def test_a_timeout_degrades_the_mailbox_and_keeps_the_credential(env: Environment) -> None:
    env.connect(ADMIN)
    env.google.refresh_mode = "timeout"

    outcome = env.service.refresh(SUPPORT)

    assert outcome.code == "PROVIDER_TIMEOUT"
    assert env.status() == "DEGRADED"
    assert env.store.get_credential(SUPPORT) is not None


def test_a_degraded_mailbox_recovers_on_the_next_successful_refresh(env: Environment) -> None:
    env.connect(ADMIN)
    env.google.refresh_mode = "timeout"
    env.service.refresh(SUPPORT)
    env.google.refresh_mode = "ok"

    assert env.service.refresh(SUPPORT).ok
    assert env.status() == "CONNECTED"
    assert env.store.mailbox_fields[SUPPORT]["last_error_code"] is None


def test_an_administrator_removing_the_mailbox_scope_withdraws_access(env: Environment) -> None:
    env.connect(ADMIN)
    env.google.refresh_mode = "reduced_scope"

    outcome = env.service.refresh(SUPPORT)

    assert outcome.code == "SCOPE_REMOVED"
    assert outcome.access_token is None
    assert env.status() == "REVOKED"
    assert env.store.get_credential(SUPPORT) is None


def test_refreshing_a_never_connected_mailbox_calls_nobody(env: Environment) -> None:
    assert env.service.refresh(SUPPORT).code == "NOT_CONNECTED"
    assert env.google.calls == []


def test_refreshing_an_unknown_mailbox_is_refused(env: Environment) -> None:
    assert env.service.refresh("mbx-does-not-exist").code == "MAILBOX_NOT_FOUND"


# ===========================================================================
# REVOKE
# ===========================================================================


def test_an_admin_revokes_a_connected_mailbox(env: Environment) -> None:
    env.connect(ADMIN)
    issued = set(env.google.refresh_tokens)

    outcome = env.service.revoke(ADMIN, SUPPORT)

    assert outcome.ok and outcome.code == "REVOKED"
    assert issued <= env.google.revoked
    assert env.store.get_credential(SUPPORT) is None
    assert env.status() == "REVOKED"
    assert env.store.mailbox_fields[SUPPORT]["credential_reference"] is None
    assert "mailbox.revoked" in env.audit_actions()


def test_local_revocation_proceeds_when_google_is_unreachable(env: Environment) -> None:
    """A person who disconnected a mailbox must not be left connected."""
    env.connect(ADMIN)
    env.google.revoke_fails = True

    outcome = env.service.revoke(ADMIN, SUPPORT)

    assert outcome.ok and outcome.code == "REVOKED_LOCALLY"
    assert env.store.get_credential(SUPPORT) is None
    assert env.status() == "REVOKED"
    event = next(e for e in env.store.audit if e["action"] == "mailbox.revoked")
    assert event["metadata"]["remote_revocation"] == "failed"


@pytest.mark.parametrize("role", ["SUPERVISOR", "AGENT", "KNOWLEDGE_MANAGER", "AUDITOR"])
def test_only_owner_and_admin_may_revoke(env: Environment, role: str) -> None:
    env.connect(ADMIN)

    outcome = env.service.revoke(member(role), SUPPORT)

    assert outcome.code == "ROLE_CANNOT_ADMINISTER_MAILBOXES"
    assert env.store.get_credential(SUPPORT) is not None
    assert env.status() == "CONNECTED"


def test_revoking_another_organizations_mailbox_is_refused(env: Environment) -> None:
    assert env.service.revoke(ADMIN, FOREIGN).code == "MAILBOX_NOT_FOUND"


def test_revoking_a_never_connected_mailbox_changes_nothing(env: Environment) -> None:
    assert env.service.revoke(ADMIN, SUPPORT).code == "NOT_CONNECTED"
    assert env.status() == "PENDING"


def test_a_revoked_mailbox_cannot_be_refreshed(env: Environment) -> None:
    env.connect(ADMIN)
    env.service.revoke(ADMIN, SUPPORT)
    calls_before = len(env.google.calls)

    assert env.service.refresh(SUPPORT).code == "MAILBOX_REVOKED"
    assert len(env.google.calls) == calls_before


# ===========================================================================
# RECONNECT
# ===========================================================================


def test_a_revoked_mailbox_can_be_reconnected(env: Environment) -> None:
    env.connect(ADMIN)
    first = env.store.get_credential(SUPPORT)
    env.service.revoke(ADMIN, SUPPORT)

    outcome = env.connect(ADMIN)
    second = env.store.get_credential(SUPPORT)

    assert outcome.ok
    assert env.status() == "CONNECTED"
    assert env.store.mailboxes[SUPPORT].revoked_at is None
    assert first is not None and second is not None
    assert first.sealed.envelope != second.sealed.envelope
    assert len([c for c in env.store.credentials.values() if c.mailbox_id == SUPPORT]) == 1


def test_a_mailbox_withdrawn_by_google_can_be_reconnected(env: Environment) -> None:
    env.connect(ADMIN)
    env.google.refresh_mode = "invalid_grant"
    env.service.refresh(SUPPORT)
    env.google.refresh_mode = "ok"

    assert env.connect(ADMIN).ok
    assert env.service.refresh(SUPPORT).ok


def test_reconnecting_a_live_mailbox_replaces_rather_than_accumulates(env: Environment) -> None:
    env.connect(ADMIN)
    env.connect(ADMIN)

    assert len(env.store.credentials) == 1
    assert env.status() == "CONNECTED"
    reconnect = [e for e in env.store.audit if e["action"] == "mailbox.connected"][-1]
    assert reconnect["metadata"]["reconnect"] is True


def test_reconnecting_with_the_wrong_account_leaves_the_mailbox_revoked(env: Environment) -> None:
    env.connect(ADMIN)
    env.service.revoke(ADMIN, SUPPORT)
    env.google.account_email = "someone.personal@gmail.example"

    assert env.connect(ADMIN).code == "WRONG_MAILBOX_AUTHORIZED"
    assert env.status() == "REVOKED"
    assert env.store.get_credential(SUPPORT) is None


def test_a_degraded_mailbox_can_be_reconnected(env: Environment) -> None:
    env.connect(ADMIN)
    env.google.refresh_mode = "timeout"
    env.service.refresh(SUPPORT)
    env.google.refresh_mode = "ok"

    assert env.connect(ADMIN).ok
    assert env.status() == "CONNECTED"


# ===========================================================================
# THE DRAFTING PROFILE
#
# The client authorized asking for `gmail.compose` so an approved answer can
# become a draft (C12). These run the whole connection against the simulated
# Google under that profile, because the scope policy agreeing is not the same
# as the credential being storable and the mailbox staying connected.
# ===========================================================================


@pytest.fixture
def drafting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROFILE_VARIABLE, "read_and_draft")


@pytest.fixture(autouse=True)
def _read_only_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every case above this point asserts read-only behaviour by default."""
    monkeypatch.delenv(PROFILE_VARIABLE, raising=False)


def test_a_compose_grant_connects_under_the_drafting_profile(
    env: Environment, drafting: None
) -> None:
    env.google.granted_scope = DRAFT_GRANT

    outcome = env.connect(ADMIN)

    assert outcome.ok, outcome.code
    assert env.status() == "CONNECTED"
    credential = env.store.get_credential(SUPPORT)
    assert credential is not None
    assert GMAIL_COMPOSE in credential.granted_scopes
    # The grant was not revoked on the way through.
    assert not (env.google.refresh_tokens & env.google.revoked)


def test_a_compose_grant_is_revoked_under_the_read_only_profile(env: Environment) -> None:
    """The deployment the client has not switched behaves exactly as before."""
    env.google.granted_scope = DRAFT_GRANT

    outcome = env.connect(ADMIN)

    assert outcome.code == "WRITE_SCOPE_GRANTED"
    assert env.google.refresh_tokens <= env.google.revoked
    assert env.store.get_credential(SUPPORT) is None
    assert env.status() == "PENDING"


def test_unticking_compose_still_connects_the_mailbox(env: Environment, drafting: None) -> None:
    """Granular consent: the mailbox is usable, and drafting is simply off."""
    env.google.granted_scope = READ_ONLY_GRANT

    outcome = env.connect(ADMIN)

    assert outcome.ok, outcome.code
    assert env.status() == "CONNECTED"
    credential = env.store.get_credential(SUPPORT)
    assert credential is not None
    assert GMAIL_COMPOSE not in credential.granted_scopes


def test_sending_is_still_refused_under_the_drafting_profile(
    env: Environment, drafting: None
) -> None:
    env.google.granted_scope = f"{DRAFT_GRANT} {SEND}"

    outcome = env.connect(ADMIN)

    assert outcome.code == "WRITE_SCOPE_GRANTED"
    assert env.google.refresh_tokens <= env.google.revoked
    assert env.store.get_credential(SUPPORT) is None


def test_a_compose_credential_keeps_refreshing(env: Environment, drafting: None) -> None:
    env.google.granted_scope = DRAFT_GRANT
    assert env.connect(ADMIN).ok

    outcome = env.service.refresh(SUPPORT)

    assert outcome.ok
    assert outcome.access_token in env.google.access_tokens
    assert env.status() == "CONNECTED"


def test_compose_being_taken_away_later_leaves_the_mailbox_connected(
    env: Environment, drafting: None
) -> None:
    """Losing an optional scope loses a capability, not the mailbox.

    The contrast is `test_an_administrator_removing_the_mailbox_scope_withdraws_access`:
    removing `gmail.readonly` is a withdrawal, because the product cannot work
    without it.
    """
    env.google.granted_scope = DRAFT_GRANT
    assert env.connect(ADMIN).ok

    env.google.granted_scope = READ_ONLY_GRANT
    outcome = env.service.refresh(SUPPORT)

    assert outcome.ok, outcome.code
    assert env.status() == "CONNECTED"
    assert env.store.get_credential(SUPPORT) is not None


def test_a_send_scope_appearing_at_refresh_withdraws_access(
    env: Environment, drafting: None
) -> None:
    env.google.granted_scope = DRAFT_GRANT
    assert env.connect(ADMIN).ok

    env.google.granted_scope = f"{DRAFT_GRANT} {SEND}"
    outcome = env.service.refresh(SUPPORT)

    assert outcome.code == "WRITE_SCOPE_GRANTED"
    assert outcome.access_token is None
    assert env.status() == "REVOKED"
    assert env.store.get_credential(SUPPORT) is None
