"""Applying the rate limits at the API boundary (C13).

One dependency per policy, declared on the routes it protects, so reading a
route tells you what limits it is under. A single global middleware was the
alternative and was rejected: it would have to guess the key from the path,
and the key is the whole point of a limit.

Two deliberate choices:

* **Only routes that already need the database.** `/healthz` and
  `/v1/capabilities` do no I/O and are reachable only from inside the VPC, so
  counting them would add a write per health check for no gain.
* **Fail closed, with a distinguishable code.** A caller that is over its
  allowance gets `RATE_LIMITED` with `Retry-After`; a caller the limiter could
  not count gets `RATE_LIMIT_UNAVAILABLE`. Both are 429 - from the caller's
  side both mean "come back" - but an operator needs to tell a busy client
  from a broken counter.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, Path

from app.api.database import _database_url, _engine
from app.api.dependencies import InternalContext, require_internal_context
from app.security.rate_limit import (
    POLICIES,
    PostgresRateLimiter,
)

MAILBOX_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{2,79}$"


@lru_cache(maxsize=1)
def _limiter(url: str) -> PostgresRateLimiter:
    return PostgresRateLimiter(_engine(url))


def get_rate_limiter() -> PostgresRateLimiter | None:
    """None when the service has no database; the route will 503 anyway."""
    url = _database_url()
    return _limiter(url) if url else None


def enforce(
    limiter: PostgresRateLimiter | None,
    policy_name: str,
    key: str,
    *,
    organization_id: str,
) -> None:
    """Count one request, or raise.

    The domain exceptions travel up to the handlers registered in
    `app.api.main`, which render them in the same refusal shape as every other
    refusal in this API - `{schema_version, ok, code}` - rather than FastAPI's
    `{detail}`. The web tier reads one shape.
    """
    if limiter is None:
        return
    if policy_name not in POLICIES:  # pragma: no cover - guards a typo at import time
        raise KeyError(policy_name)
    limiter.check(policy_name, key, organization_id=organization_id)


LimiterDep = Annotated[Any, Depends(get_rate_limiter)]
ContextDep = Annotated[InternalContext, Depends(require_internal_context)]


def _actor_key(context: InternalContext) -> str:
    """A person when there is one, otherwise the organization.

    System-initiated work has no actor, and attributing it to a person would
    both misattribute it and let scheduled work exhaust someone's allowance.
    """
    return context.actor_membership_id or f"system:{context.organization_id}"


def limit_review(context: ContextDep, limiter: LimiterDep) -> None:
    enforce(
        limiter, "review_decision", _actor_key(context), organization_id=context.organization_id
    )


def limit_draft_preview(context: ContextDep, limiter: LimiterDep) -> None:
    enforce(limiter, "draft_preview", _actor_key(context), organization_id=context.organization_id)


def limit_ai_settings_write(context: ContextDep, limiter: LimiterDep) -> None:
    enforce(
        limiter,
        "ai_settings_write",
        context.organization_id,
        organization_id=context.organization_id,
    )


def limit_credential_verify(context: ContextDep, limiter: LimiterDep) -> None:
    enforce(
        limiter,
        "credential_verify",
        context.organization_id,
        organization_id=context.organization_id,
    )


def limit_mailbox_complete(context: ContextDep, limiter: LimiterDep) -> None:
    """The OAuth callback carries a state, not a mailbox id, so the allowance
    is the organization's. The state itself is already single-use (C06)."""
    enforce(
        limiter,
        "mailbox_connect",
        f"complete:{context.organization_id}",
        organization_id=context.organization_id,
    )


def limit_mailbox_connect(
    context: ContextDep,
    limiter: LimiterDep,
    mailbox_id: Annotated[str, Path(pattern=MAILBOX_ID_PATTERN)],
) -> None:
    enforce(limiter, "mailbox_connect", mailbox_id, organization_id=context.organization_id)


# --------------------------------------------------------------------------
# Request size
# --------------------------------------------------------------------------

# The largest body this API accepts. The biggest legitimate payload is a draft
# body (20,000 characters, C12) inside a small JSON envelope, so 256 KiB is
# roughly an order of magnitude of headroom. Without a limit, a single request
# can make the service allocate as much memory as the client cares to send.
MAX_REQUEST_BYTES = 256 * 1024


class RequestSizeLimit:
    """Refuse an oversized body before anything parses it.

    Pure ASGI rather than `BaseHTTPMiddleware`: this has to answer before the
    body is read, and it must also handle a client that lies about - or omits -
    `Content-Length` by counting what actually arrives.
    """

    def __init__(self, app: Any, *, max_bytes: int = MAX_REQUEST_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        declared = 0
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.max_bytes + 1
                break
        if declared > self.max_bytes:
            await _too_large(send)
            return

        received = 0
        too_large = False

        async def counted() -> Any:
            nonlocal received, too_large
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    # Stop the stream rather than hand on a truncated body.
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, counted, send)


async def _too_large(send: Any) -> None:
    body = b'{"schema_version":"v1","ok":false,"code":"REQUEST_TOO_LARGE"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
