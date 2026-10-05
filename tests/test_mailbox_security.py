"""Credential custody, scope policy, mailbox access, and the Google client.

Unit-level companions to the C06 lifecycle suite. Each one pins a property the
connection flow depends on but does not itself exercise exhaustively.
"""

from __future__ import annotations

import base64
import os

import pytest

from app.mailbox.access import (
    Actor,
    MailboxAction,
    MailboxFacts,
    PermissionGrant,
    authorize_mailbox,
)
from app.mailbox.google import (
    GoogleOAuthClient,
    HttpResponse,
    InvalidGrant,
    OAuthClientConfig,
    ProviderError,
    TokenGrant,
    build_authorization_url,
    pkce_challenge,
    pkce_pair,
)
from app.mailbox.scopes import (
    EMAIL,
    GMAIL_READONLY,
    OPENID,
    REQUIRED_SCOPES,
    WRITE_CAPABLE_SCOPES,
    ScopeRefusal,
    evaluate_granted_scopes,
    normalize_scopes,
    requested_scope_string,
    require_acceptable_scopes,
)
from app.mailbox.vault import SealedCredential, TokenVault, VaultError, vault_from_environment

ORG = "org-1"
MAILBOX = "mbx-1"


def binding(**overrides):
    return {
        "organization_id": ORG,
        "mailbox_id": MAILBOX,
        "purpose": "gmail-refresh-token",
        **overrides,
    }


@pytest.fixture
def vault() -> TokenVault:
    return TokenVault({"k1": os.urandom(32)}, active_key_id="k1")


# ===========================================================================
# Vault
# ===========================================================================


def test_a_sealed_credential_opens_for_its_own_binding(vault: TokenVault) -> None:
    sealed = vault.seal("1//0refresh-token-value", **binding())
    assert vault.open(sealed, **binding()) == "1//0refresh-token-value"


def test_the_envelope_does_not_contain_the_plaintext(vault: TokenVault) -> None:
    sealed = vault.seal("1//0refresh-token-value", **binding())
    assert "refresh-token-value" not in sealed.envelope
    assert "refresh-token-value" not in repr(sealed)


def test_sealing_the_same_value_twice_yields_different_ciphertext(vault: TokenVault) -> None:
    """A fixed nonce would let equal tokens be recognised across mailboxes."""
    assert vault.seal("same", **binding()).envelope != vault.seal("same", **binding()).envelope


@pytest.mark.parametrize(
    "wrong",
    [
        {"mailbox_id": "mbx-2"},
        {"organization_id": "org-2"},
        {"purpose": "gmail-pkce-verifier"},
    ],
)
def test_a_credential_does_not_open_under_any_other_binding(vault: TokenVault, wrong: dict) -> None:
    sealed = vault.seal("1//0refresh", **binding())
    with pytest.raises(VaultError, match="different mailbox or has been altered"):
        vault.open(sealed, **binding(**wrong))


def test_a_tampered_envelope_does_not_open(vault: TokenVault) -> None:
    sealed = vault.seal("1//0refresh", **binding())
    version, encoded = sealed.envelope.split(".", 1)
    raw = bytearray(base64.urlsafe_b64decode(encoded))
    raw[-1] ^= 0x01
    tampered = SealedCredential(
        sealed.key_id, f"{version}.{base64.urlsafe_b64encode(bytes(raw)).decode()}"
    )

    with pytest.raises(VaultError):
        vault.open(tampered, **binding())


@pytest.mark.parametrize(
    "envelope",
    ["", "v1.", "v2.AAAA", "no-version-separator", "v1.!!!not-base64!!!", "v1.AAAA"],
)
def test_malformed_envelopes_are_errors_not_empty_strings(vault: TokenVault, envelope: str) -> None:
    with pytest.raises(VaultError):
        vault.open(SealedCredential("k1", envelope), **binding())


def test_an_unknown_key_id_is_an_error(vault: TokenVault) -> None:
    sealed = vault.seal("1//0refresh", **binding())
    with pytest.raises(VaultError, match="not in the keyring"):
        vault.open(SealedCredential("retired-key", sealed.envelope), **binding())


def test_an_empty_credential_is_never_sealed(vault: TokenVault) -> None:
    with pytest.raises(VaultError, match="empty"):
        vault.seal("", **binding())


def test_incomplete_binding_is_refused(vault: TokenVault) -> None:
    with pytest.raises(VaultError, match="requires organization, mailbox, and purpose"):
        vault.seal("x", organization_id=ORG, mailbox_id="", purpose="p")


def test_rotation_keeps_old_credentials_readable_and_reseals_under_the_new_key() -> None:
    old_key, new_key = os.urandom(32), os.urandom(32)
    before = TokenVault({"k1": old_key}, active_key_id="k1")
    sealed = before.seal("1//0refresh", **binding())

    after = TokenVault({"k2": new_key, "k1": old_key}, active_key_id="k2")

    assert after.open(sealed, **binding()) == "1//0refresh"
    assert after.needs_rotation(sealed)
    resealed = after.reseal(sealed, **binding())
    assert resealed.key_id == "k2"
    assert not after.needs_rotation(resealed)
    assert TokenVault({"k2": new_key}, "k2").open(resealed, **binding()) == "1//0refresh"


@pytest.mark.parametrize(
    ("keys", "active", "message"),
    [
        ({}, "k1", "at least one key"),
        ({"k1": b"short"}, "k1", "AES-256 requires 32"),
        ({"Bad Id": os.urandom(32)}, "Bad Id", "not a valid identifier"),
        ({"k1": os.urandom(32)}, "k2", "not in the keyring"),
    ],
)
def test_an_invalid_keyring_is_refused(keys: dict, active: str, message: str) -> None:
    with pytest.raises(VaultError, match=message):
        TokenVault(keys, active_key_id=active)


def test_the_keyring_is_read_from_the_environment_with_the_first_key_active() -> None:
    first, second = os.urandom(32), os.urandom(32)
    value = f"k2:{base64.b64encode(first).decode()},k1:{base64.b64encode(second).decode()}"

    vault = vault_from_environment({"MAILBOX_TOKEN_ENCRYPTION_KEY": value})

    assert vault.active_key_id == "k2"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "not configured"),
        ("no-separator", "key-id:base64key"),
        ("k1:not base64!", "not valid base64"),
        (
            f"k1:{base64.b64encode(b'x' * 32).decode()},k1:{base64.b64encode(b'y' * 32).decode()}",
            "appears twice",
        ),
    ],
)
def test_a_misconfigured_keyring_fails_closed(value: str, message: str) -> None:
    with pytest.raises(VaultError, match=message):
        vault_from_environment({"MAILBOX_TOKEN_ENCRYPTION_KEY": value})


# ===========================================================================
# Scopes
# ===========================================================================


def test_the_requested_scopes_are_exactly_the_read_only_set() -> None:
    requested = set(requested_scope_string().split())
    assert requested == set(REQUIRED_SCOPES)
    assert not requested & WRITE_CAPABLE_SCOPES


def test_scopes_normalise_from_strings_lists_and_aliases() -> None:
    assert normalize_scopes(f"{GMAIL_READONLY} openid email") == REQUIRED_SCOPES
    assert normalize_scopes([GMAIL_READONLY, OPENID, EMAIL]) == REQUIRED_SCOPES
    assert normalize_scopes(None) == frozenset()
    assert normalize_scopes("  ") == frozenset()


@pytest.mark.parametrize(
    ("granted", "code"),
    [
        (f"{OPENID} {EMAIL}", "MAILBOX_SCOPE_DENIED"),
        (f"{GMAIL_READONLY} {OPENID}", "IDENTITY_SCOPE_DENIED"),
        (
            f"{GMAIL_READONLY} {OPENID} {EMAIL} https://www.googleapis.com/auth/gmail.modify",
            "WRITE_SCOPE_GRANTED",
        ),
        (
            f"{GMAIL_READONLY} {OPENID} {EMAIL} https://www.googleapis.com/auth/calendar",
            "UNEXPECTED_SCOPE_GRANTED",
        ),
        ("", "MAILBOX_SCOPE_DENIED"),
    ],
)
def test_each_scope_mismatch_has_a_specific_reason(granted: str, code: str) -> None:
    decision = evaluate_granted_scopes(granted)
    assert not decision.acceptable
    assert decision.reason_code == code
    with pytest.raises(ScopeRefusal) as refused:
        require_acceptable_scopes(granted)
    assert refused.value.reason == code


def test_a_write_scope_outranks_a_missing_scope_in_the_reason() -> None:
    """The more dangerous fact is the one reported."""
    decision = evaluate_granted_scopes(f"{OPENID} https://mail.google.com/")
    assert decision.reason_code == "WRITE_SCOPE_GRANTED"


def test_the_exact_read_only_grant_is_acceptable() -> None:
    assert require_acceptable_scopes(f"{GMAIL_READONLY} {OPENID} {EMAIL}") == REQUIRED_SCOPES


# ===========================================================================
# Mailbox access
# ===========================================================================


def actor(role: str, **overrides) -> Actor:
    return Actor(
        **{
            "membership_id": "m-1",
            "organization_id": ORG,
            "role": role,
            "status": "ACTIVE",
            **overrides,
        }
    )


MAILBOX_FACTS = MailboxFacts(MAILBOX, ORG, "CONNECTED")


def grant(**overrides) -> PermissionGrant:
    return PermissionGrant(
        **{
            "mailbox_id": MAILBOX,
            "membership_id": "m-1",
            "can_view": True,
            "can_action": True,
            **overrides,
        }
    )


@pytest.mark.parametrize(
    ("role", "action", "with_grant", "allowed"),
    [
        ("OWNER", MailboxAction.CONNECT, False, True),
        ("ADMIN", MailboxAction.REVOKE, False, True),
        ("SUPERVISOR", MailboxAction.CONNECT, True, False),
        ("AGENT", MailboxAction.REVOKE, True, False),
        ("OWNER", MailboxAction.VIEW, False, True),
        ("AUDITOR", MailboxAction.VIEW, False, True),
        ("AUDITOR", MailboxAction.ACT, True, False),
        ("SUPERVISOR", MailboxAction.VIEW, False, False),
        ("SUPERVISOR", MailboxAction.VIEW, True, True),
        ("AGENT", MailboxAction.ACT, False, False),
        ("AGENT", MailboxAction.ACT, True, True),
        ("KNOWLEDGE_MANAGER", MailboxAction.VIEW, True, False),
    ],
)
def test_the_mailbox_access_matrix(
    role: str, action: MailboxAction, with_grant: bool, allowed: bool
) -> None:
    decision = authorize_mailbox(
        actor(role), MAILBOX_FACTS, action, grant() if with_grant else None
    )
    assert decision.allowed is allowed, decision.reason


def test_organization_membership_alone_never_grants_mailbox_access() -> None:
    for role in ("SUPERVISOR", "AGENT"):
        decision = authorize_mailbox(actor(role), MAILBOX_FACTS, MailboxAction.VIEW)
        assert decision.reason == "NO_MAILBOX_PERMISSION"


def test_a_grant_for_one_mailbox_says_nothing_about_another() -> None:
    decision = authorize_mailbox(
        actor("AGENT"), MAILBOX_FACTS, MailboxAction.VIEW, grant(mailbox_id="mbx-other")
    )
    assert decision.reason == "GRANT_DOES_NOT_MATCH"


def test_a_grant_for_another_person_is_not_borrowed() -> None:
    decision = authorize_mailbox(
        actor("AGENT"), MAILBOX_FACTS, MailboxAction.VIEW, grant(membership_id="m-someone-else")
    )
    assert decision.reason == "GRANT_DOES_NOT_MATCH"


def test_view_only_permission_does_not_allow_action() -> None:
    decision = authorize_mailbox(
        actor("AGENT"), MAILBOX_FACTS, MailboxAction.ACT, grant(can_action=False)
    )
    assert decision.reason == "NO_MAILBOX_ACTION_PERMISSION"


def test_an_action_grant_without_view_does_not_allow_action() -> None:
    decision = authorize_mailbox(
        actor("AGENT"), MAILBOX_FACTS, MailboxAction.ACT, grant(can_view=False, can_action=True)
    )
    assert not decision.allowed


def test_no_role_crosses_an_organization_boundary() -> None:
    foreign = MailboxFacts(MAILBOX, "org-other", "CONNECTED")
    for role in ("OWNER", "ADMIN", "AUDITOR"):
        for action in MailboxAction:
            decision = authorize_mailbox(actor(role), foreign, action, grant())
            assert decision.reason == "MAILBOX_NOT_IN_ORGANIZATION"


def test_a_revoked_mailbox_can_be_viewed_but_not_acted_on() -> None:
    revoked = MailboxFacts(MAILBOX, ORG, "REVOKED")
    assert authorize_mailbox(actor("ADMIN"), revoked, MailboxAction.VIEW).allowed
    assert authorize_mailbox(actor("ADMIN"), revoked, MailboxAction.ACT).reason == "MAILBOX_REVOKED"


def test_unauthenticated_inactive_and_missing_are_refused() -> None:
    assert authorize_mailbox(None, MAILBOX_FACTS, MailboxAction.VIEW).reason == "NOT_AUTHENTICATED"
    assert authorize_mailbox(actor("ADMIN"), None, MailboxAction.VIEW).reason == "MAILBOX_NOT_FOUND"
    suspended = actor("ADMIN", status="SUSPENDED")
    assert (
        authorize_mailbox(suspended, MAILBOX_FACTS, MailboxAction.VIEW).reason
        == "MEMBERSHIP_NOT_ACTIVE"
    )


# ===========================================================================
# Google client
# ===========================================================================


CONFIG = OAuthClientConfig(
    client_id="id.apps.googleusercontent.com",
    client_secret="client-secret-value",
    redirect_uri="https://support.acme.example/api/mailboxes/oauth/callback",
)


class Scripted:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response

    def request(self, method, url, *, form=None, bearer=None, timeout=15.0):
        return self.response


def test_pkce_matches_the_rfc_7636_test_vector() -> None:
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_generated_pkce_verifiers_are_within_the_rfc_bounds_and_unique() -> None:
    pairs = [pkce_pair() for _ in range(20)]
    for verifier, challenge in pairs:
        assert 43 <= len(verifier) <= 128
        assert challenge == pkce_challenge(verifier)
    assert len({verifier for verifier, _ in pairs}) == 20


def test_the_client_secret_never_appears_in_a_repr_or_the_authorization_url() -> None:
    url = build_authorization_url(CONFIG, state="s", code_challenge="c", login_hint="a@b.example")
    assert "client-secret-value" not in repr(CONFIG)
    assert "client-secret-value" not in url


def test_tokens_never_appear_in_a_grant_repr() -> None:
    grant = TokenGrant(
        access_token="ya29.secret", refresh_token="1//0secret", scope="", expires_in=1
    )
    assert "secret" not in repr(grant)


@pytest.mark.parametrize(
    "redirect",
    ["http://support.acme.example/callback", "ftp://x", ""],
)
def test_a_non_https_redirect_is_refused_outside_localhost(redirect: str) -> None:
    with pytest.raises(ValueError):
        OAuthClientConfig(client_id="id", client_secret="s", redirect_uri=redirect)


def test_localhost_redirect_is_allowed_for_development() -> None:
    OAuthClientConfig(client_id="id", client_secret="s", redirect_uri="http://localhost:3000/cb")


@pytest.mark.parametrize(
    ("response", "expected_type", "code", "transient"),
    [
        (HttpResponse(400, {"error": "invalid_grant"}), InvalidGrant, "INVALID_GRANT", False),
        (HttpResponse(401, {"error": "unauthorized_client"}), InvalidGrant, "INVALID_GRANT", False),
        (HttpResponse(503, {}), ProviderError, "PROVIDER_UNAVAILABLE", True),
        (HttpResponse(429, {}), ProviderError, "PROVIDER_UNAVAILABLE", True),
        (
            HttpResponse(401, {"error": "invalid_client"}),
            ProviderError,
            "OAUTH_CLIENT_REJECTED",
            False,
        ),
        (
            HttpResponse(400, {"error": "invalid_request"}),
            ProviderError,
            "TOKEN_REQUEST_REJECTED",
            False,
        ),
    ],
)
def test_token_endpoint_errors_are_classified(response, expected_type, code, transient) -> None:
    client = GoogleOAuthClient(CONFIG, transport=Scripted(response))
    with pytest.raises(expected_type) as raised:
        client.refresh("1//0token")
    assert raised.value.code == code
    assert raised.value.transient is transient


def test_a_provider_error_message_carries_no_token_material() -> None:
    client = GoogleOAuthClient(
        CONFIG, transport=Scripted(HttpResponse(400, {"error": "invalid_grant"}))
    )
    with pytest.raises(ProviderError) as raised:
        client.refresh("1//0very-secret-refresh-token")
    assert "very-secret" not in str(raised.value)


def test_a_success_response_without_an_access_token_is_malformed() -> None:
    client = GoogleOAuthClient(CONFIG, transport=Scripted(HttpResponse(200, {"expires_in": 3600})))
    with pytest.raises(ProviderError) as raised:
        client.refresh("1//0token")
    assert raised.value.code == "TOKEN_RESPONSE_MALFORMED"


def test_revoking_an_already_invalid_token_counts_as_revoked() -> None:
    client = GoogleOAuthClient(
        CONFIG, transport=Scripted(HttpResponse(400, {"error": "invalid_token"}))
    )
    client.revoke("1//0gone")


def test_a_failed_revocation_is_reported() -> None:
    client = GoogleOAuthClient(CONFIG, transport=Scripted(HttpResponse(503, {})))
    with pytest.raises(ProviderError) as raised:
        client.revoke("1//0token")
    assert raised.value.code == "REVOCATION_FAILED" and raised.value.transient


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (HttpResponse(401, {}), "MAILBOX_PROFILE_FORBIDDEN"),
        (HttpResponse(403, {}), "MAILBOX_PROFILE_FORBIDDEN"),
        (HttpResponse(500, {}), "MAILBOX_PROFILE_UNAVAILABLE"),
        (HttpResponse(200, {"emailAddress": "not-an-address"}), "MAILBOX_PROFILE_MALFORMED"),
    ],
)
def test_profile_failures_are_classified(response: HttpResponse, code: str) -> None:
    client = GoogleOAuthClient(CONFIG, transport=Scripted(response))
    with pytest.raises(ProviderError) as raised:
        client.mailbox_address("ya29.token")
    assert raised.value.code == code


def test_the_profile_address_is_normalised() -> None:
    client = GoogleOAuthClient(
        CONFIG, transport=Scripted(HttpResponse(200, {"emailAddress": "  Support@ACME.example "}))
    )
    assert client.mailbox_address("ya29.token") == "support@acme.example"


def test_an_empty_authorization_code_is_refused_without_a_request() -> None:
    class Forbidden:
        def request(self, *args, **kwargs):
            raise AssertionError("no request should be made")

    with pytest.raises(ProviderError) as raised:
        GoogleOAuthClient(CONFIG, transport=Forbidden()).exchange_code("", "verifier")
    assert raised.value.code == "AUTHORIZATION_CODE_MISSING"


@pytest.mark.parametrize(
    ("raised", "code"),
    [("ReadTimeout", "PROVIDER_TIMEOUT"), ("ConnectError", "PROVIDER_UNREACHABLE")],
)
def test_transport_failures_do_not_chain_the_request(monkeypatch, raised, code):
    """An httpx error holds the request: bearer header and form body (C10 finding)."""
    import httpx

    from app.mailbox.google import HttpxTransport, ProviderError

    def fail(*args, **kwargs):
        request = httpx.Request("POST", "https://oauth2.googleapis.com/token",
                                data={"refresh_token": "1//refresh-token-value"})
        raise getattr(httpx, raised)("failed", request=request)

    monkeypatch.setattr(httpx, "request", fail)
    with pytest.raises(ProviderError) as error:
        HttpxTransport().request("POST", "https://oauth2.googleapis.com/token",
                                 form={"refresh_token": "1//refresh-token-value"})
    assert error.value.code == code
    assert error.value.__cause__ is None and error.value.__context__ is None
