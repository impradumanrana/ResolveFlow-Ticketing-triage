"""Reading and writing the client's API key in Secret Manager.

The key exists in memory only while a call needs it:

* `SecretValue` hides it from `repr`, `str`, formatting, and pickling, so an
  accidental log line or exception message prints a mask.
* The cache keeps it for minutes, not forever, so a rotated key takes effect
  without a restart, and a key the provider rejects is dropped at once.
* Nothing here reads an environment variable. The MVP's `OPENAI_API_KEY` is a
  developer convenience; the client deployment never consults it (C-D009).

Secret Manager is plain HTTPS, called over an injectable transport with the
runtime's own identity from the metadata server, as C06 does for Google OAuth.
"""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.gateway.errors import FailureCode, GatewayFailure

SECRET_NAME = re.compile(r"^projects/[a-z0-9-]{1,63}/secrets/[A-Za-z0-9_-]{1,255}$")
SECRET_MANAGER_API = "https://secretmanager.googleapis.com/v1"
METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
CACHE_SECONDS = 300
MAX_KEY_LENGTH = 512
MIN_KEY_LENGTH = 20


class SecretValue:
    """A credential that refuses to be printed."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    @property
    def hint(self) -> str:
        return mask(self._value)

    def __repr__(self) -> str:
        return f"SecretValue({self.hint})"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __reduce__(self) -> Any:
        raise TypeError("Credentials cannot be serialised.")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretValue) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("SecretValue", self._value))


def mask(value: str | None) -> str:
    """Only the last four characters, and only for a key long enough to hide the rest."""
    if not value or len(value) < MIN_KEY_LENGTH:
        return "not set"
    return f"••••{value[-4:]}"


def last_four(value: str) -> str:
    return value[-4:]


def validate_new_key(value: str) -> str:
    """Shape checks before a key is stored. The provider decides if it works."""
    candidate = value.strip()
    if not MIN_KEY_LENGTH <= len(candidate) <= MAX_KEY_LENGTH:
        raise GatewayFailure(FailureCode.CREDENTIAL_INVALID)
    if any(character.isspace() or not character.isprintable() for character in candidate):
        raise GatewayFailure(FailureCode.CREDENTIAL_INVALID)
    return candidate


class SecretSource(Protocol):
    def access(self, secret_name: str) -> SecretValue: ...

    def add_version(self, secret_name: str, value: SecretValue) -> str: ...


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: dict[str, Any]


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> HttpReply: ...


class HttpxTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> HttpReply:
        import httpx

        response = None
        try:
            response = httpx.request(method, url, headers=headers, json=json, timeout=timeout)
        except httpx.HTTPError:
            # Not chained: an httpx error holds the request, and the request
            # holds the bearer token.
            pass
        if response is None:
            raise GatewayFailure(FailureCode.CREDENTIAL_ACCESS_DENIED)
        try:
            body = response.json()
        except ValueError:
            body = {}
        return HttpReply(response.status_code, body if isinstance(body, dict) else {})


class SecretManagerSource:
    """Secret Manager over REST, authenticated as the running service."""

    def __init__(
        self,
        transport: HttpTransport | None = None,
        *,
        token_provider: Callable[[], str] | None = None,
        timeout: float = 5.0,
    ):
        self.transport = transport or HttpxTransport()
        self.timeout = timeout
        self._token_provider = token_provider or self._metadata_token

    def _metadata_token(self) -> str:
        reply = self.transport.request(
            "GET",
            METADATA_TOKEN_URL,
            headers={"Metadata-Flavor": "Google"},
            timeout=self.timeout,
        )
        token = reply.body.get("access_token") if reply.status == 200 else None
        if not isinstance(token, str) or not token:
            raise GatewayFailure(FailureCode.CREDENTIAL_ACCESS_DENIED, status=reply.status)
        return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    def access(self, secret_name: str) -> SecretValue:
        if not SECRET_NAME.fullmatch(secret_name):
            raise GatewayFailure(FailureCode.CREDENTIAL_MISSING)
        reply = self.transport.request(
            "GET",
            f"{SECRET_MANAGER_API}/{secret_name}/versions/latest:access",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if reply.status == 200:
            encoded = (reply.body.get("payload") or {}).get("data")
            if not isinstance(encoded, str):
                raise GatewayFailure(FailureCode.CREDENTIAL_MISSING, status=reply.status)
            value = ""
            try:
                value = base64.b64decode(encoded, validate=True).decode("utf-8").strip()
            except ValueError:
                # Not chained: a UnicodeDecodeError carries the raw secret bytes.
                pass
            if not value:
                raise GatewayFailure(FailureCode.CREDENTIAL_MISSING)
            return SecretValue(value)
        raise GatewayFailure(_access_failure(reply), status=reply.status)

    def add_version(self, secret_name: str, value: SecretValue) -> str:
        if not SECRET_NAME.fullmatch(secret_name):
            raise GatewayFailure(FailureCode.CREDENTIAL_MISSING)
        encoded = base64.b64encode(value.reveal().encode("utf-8")).decode("ascii")
        reply = self.transport.request(
            "POST",
            f"{SECRET_MANAGER_API}/{secret_name}:addVersion",
            headers=self._headers(),
            json={"payload": {"data": encoded}},
            timeout=self.timeout,
        )
        version = reply.body.get("name")
        if reply.status != 200 or not isinstance(version, str):
            raise GatewayFailure(_access_failure(reply), status=reply.status)
        return version


def _access_failure(reply: HttpReply) -> FailureCode:
    # Secret Manager answers FAILED_PRECONDITION when the latest version is
    # disabled or destroyed: the key existed and has been withdrawn.
    status = str((reply.body.get("error") or {}).get("status") or "")
    if reply.status == 400 and status == "FAILED_PRECONDITION":
        return FailureCode.CREDENTIAL_EXPIRED
    if reply.status == 404:
        return FailureCode.CREDENTIAL_MISSING
    return FailureCode.CREDENTIAL_ACCESS_DENIED


class CredentialCache:
    """Short-lived, per secret, and dropped the moment a provider rejects the key."""

    def __init__(
        self,
        source: SecretSource,
        *,
        ttl_seconds: float = CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.source = source
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._entries: dict[str, tuple[float, SecretValue]] = {}

    def get(self, secret_name: str) -> SecretValue:
        entry = self._entries.get(secret_name)
        if entry and self.clock() - entry[0] < self.ttl_seconds:
            return entry[1]
        value = self.source.access(secret_name)
        self._entries[secret_name] = (self.clock(), value)
        return value

    def invalidate(self, secret_name: str) -> None:
        self._entries.pop(secret_name, None)
