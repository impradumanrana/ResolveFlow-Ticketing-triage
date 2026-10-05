"""Google OAuth 2.0 and Gmail profile calls, over an injectable transport.

No Google SDK: the token, revoke, and profile endpoints are plain HTTPS, and
this is the most security-sensitive path in the product, so the dependency
surface stays where it is. The transport is injected so every provider
behaviour - a denied scope, a revoked grant, a timeout, the wrong account -
is reproducible in tests without a real Google account.

Tokens never appear in exception messages, reprs, or logs. A provider error
carries a stable code and whether it is worth retrying, nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

from app.mailbox.scopes import requested_scope_string

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GMAIL_PROFILE_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

DEFAULT_TIMEOUT_SECONDS = 15.0


class ProviderError(Exception):
    """A provider call failed. `transient` says whether retrying could help."""

    def __init__(self, code: str, *, transient: bool, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.status = status


class InvalidGrant(ProviderError):
    """The grant was revoked, expired, or never valid. Retrying cannot help."""

    def __init__(self, status: int | None = None):
        super().__init__("INVALID_GRANT", transient=False, status=status)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: dict[str, Any]


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        form: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        bearer: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> HttpResponse: ...


class HttpxTransport:
    """Production transport. Network failures become transient provider errors."""

    def request(
        self,
        method: str,
        url: str,
        *,
        form: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        bearer: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> HttpResponse:
        import httpx

        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        response = None
        failure = None
        try:
            # `json` added for the Gmail API (C07), which takes JSON bodies;
            # OAuth endpoints take form encoding. Never both.
            response = httpx.request(
                method, url, data=form, json=json, headers=headers, timeout=timeout
            )
        except httpx.TimeoutException:
            failure = ProviderError("PROVIDER_TIMEOUT", transient=True)
        except httpx.TransportError:
            failure = ProviderError("PROVIDER_UNREACHABLE", transient=True)
        # Raised outside the handler so nothing is chained: an httpx error holds
        # the request, whose headers carry the bearer token and whose body can
        # carry the refresh token and client secret (found in C10).
        if failure is not None or response is None:
            raise failure or ProviderError("PROVIDER_UNREACHABLE", transient=True)

        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        return HttpResponse(
            status=response.status_code, body=body if isinstance(body, dict) else {}
        )


@dataclass(frozen=True)
class OAuthClientConfig:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str

    def __post_init__(self) -> None:
        if not self.client_id or not self.client_secret or not self.redirect_uri:
            raise ValueError("Mailbox OAuth client id, secret, and redirect URI are all required.")
        if not self.redirect_uri.startswith("https://") and not self.redirect_uri.startswith(
            "http://localhost"
        ):
            raise ValueError("The mailbox OAuth redirect URI must be HTTPS outside localhost.")


@dataclass(frozen=True)
class TokenGrant:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    scope: str
    expires_in: int


@dataclass(frozen=True)
class AccessGrant:
    access_token: str = field(repr=False)
    scope: str | None
    expires_in: int


def pkce_challenge(verifier: str) -> str:
    """S256 code challenge for a verifier (RFC 7636 section 4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge.

    96 characters from the unreserved set, inside RFC 7636's 43-128 bound.
    """
    verifier = secrets.token_urlsafe(72)[:96]
    return verifier, pkce_challenge(verifier)


def build_authorization_url(
    config: OAuthClientConfig, *, state: str, code_challenge: str, login_hint: str
) -> str:
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": requested_scope_string(),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # A refresh token is only issued for offline access, and only reliably
        # when consent is shown. Without one the mailbox stops syncing in an hour.
        "access_type": "offline",
        "prompt": "consent select_account",
        # Never merge previously granted scopes back in: that is how a token
        # acquires gmail.send without anyone asking for it here.
        "include_granted_scopes": "false",
        # Pre-selects the intended mailbox. A hint, not a guarantee: the
        # returned account is verified after the exchange.
        "login_hint": login_hint,
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def _token_error(response: HttpResponse) -> ProviderError:
    error = str(response.body.get("error") or "").lower()
    if error in {"invalid_grant", "unauthorized_client"}:
        return InvalidGrant(status=response.status)
    if response.status >= 500 or response.status == 429:
        return ProviderError("PROVIDER_UNAVAILABLE", transient=True, status=response.status)
    if error == "invalid_client":
        return ProviderError("OAUTH_CLIENT_REJECTED", transient=False, status=response.status)
    return ProviderError("TOKEN_REQUEST_REJECTED", transient=False, status=response.status)


class GoogleOAuthClient:
    def __init__(self, config: OAuthClientConfig, transport: Transport | None = None):
        self._config = config
        self._transport = transport or HttpxTransport()

    @property
    def config(self) -> OAuthClientConfig:
        return self._config

    def exchange_code(self, code: str, code_verifier: str) -> TokenGrant:
        if not code:
            raise ProviderError("AUTHORIZATION_CODE_MISSING", transient=False)
        response = self._transport.request(
            "POST",
            TOKEN_ENDPOINT,
            form={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "redirect_uri": self._config.redirect_uri,
            },
        )
        if response.status != 200:
            raise _token_error(response)

        access_token = response.body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ProviderError("TOKEN_RESPONSE_MALFORMED", transient=False)
        refresh_token = response.body.get("refresh_token")
        return TokenGrant(
            access_token=access_token,
            refresh_token=refresh_token
            if isinstance(refresh_token, str) and refresh_token
            else None,
            scope=str(response.body.get("scope") or ""),
            expires_in=int(response.body.get("expires_in") or 0),
        )

    def refresh(self, refresh_token: str) -> AccessGrant:
        response = self._transport.request(
            "POST",
            TOKEN_ENDPOINT,
            form={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            },
        )
        if response.status != 200:
            raise _token_error(response)

        access_token = response.body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ProviderError("TOKEN_RESPONSE_MALFORMED", transient=False)
        scope = response.body.get("scope")
        return AccessGrant(
            access_token=access_token,
            scope=str(scope) if scope else None,
            expires_in=int(response.body.get("expires_in") or 0),
        )

    def revoke(self, token: str) -> None:
        """Revoke at Google. Revoking a refresh token revokes its access tokens."""
        response = self._transport.request("POST", REVOKE_ENDPOINT, form={"token": token})
        # 400 invalid_token means it is already gone, which is the goal.
        if response.status == 200 or (
            response.status == 400 and response.body.get("error") == "invalid_token"
        ):
            return
        raise ProviderError(
            "REVOCATION_FAILED", transient=response.status >= 500, status=response.status
        )

    def mailbox_address(self, access_token: str) -> str:
        """The address the token actually reads - not the one we asked for."""
        response = self._transport.request("GET", GMAIL_PROFILE_ENDPOINT, bearer=access_token)
        if response.status in (401, 403):
            raise ProviderError(
                "MAILBOX_PROFILE_FORBIDDEN", transient=False, status=response.status
            )
        if response.status != 200:
            raise ProviderError(
                "MAILBOX_PROFILE_UNAVAILABLE",
                transient=response.status >= 500,
                status=response.status,
            )
        address = response.body.get("emailAddress")
        if not isinstance(address, str) or "@" not in address:
            raise ProviderError("MAILBOX_PROFILE_MALFORMED", transient=False)
        return address.strip().lower()
