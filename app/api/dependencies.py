from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

ORGANIZATION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,79}$")
MEMBERSHIP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,79}$")

# Mirrors the membership_role enum in migration 20260915_0002 and the TypeScript
# role union. An unrecognised role is rejected rather than defaulted.
KNOWN_ROLES = frozenset(
    {
        "OWNER",
        "ADMIN",
        "SUPERVISOR",
        "AGENT",
        "KNOWLEDGE_MANAGER",
        "AUDITOR",
    }
)


@dataclass(frozen=True)
class InternalContext:
    """Identity as the web tier derived it from an authenticated session.

    Every field arrives from the Next.js BFF, which reads it from the database
    on each request. None of it originates from the browser: the BFF builds
    these headers from its own `AuthContext` and forwards nothing from the
    incoming request (see apps/web/src/lib/bff/ai-api.ts).

    This service does not re-check membership - it has no database - so its
    guarantee is narrower and worth stating plainly: it authenticates *the
    calling service*, and refuses malformed or unknown context. Being reachable
    only from inside the VPC (C02) is what makes that sufficient.
    """

    organization_id: str
    request_id: str
    actor_membership_id: str | None
    actor_role: str | None


def require_internal_context(
    authorization: str | None = Header(default=None),
    x_resolveflow_organization_id: str | None = Header(default=None),
    x_resolveflow_membership_id: str | None = Header(default=None),
    x_resolveflow_role: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
) -> InternalContext:
    """Authenticate the web/BFF service and accept its server-derived context."""

    configured_token = os.getenv("RESOLVEFLOW_INTERNAL_API_TOKEN", "").strip()
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API authentication is not configured.",
        )

    supplied_token = ""
    if authorization and authorization.startswith("Bearer "):
        supplied_token = authorization.removeprefix("Bearer ").strip()
    if not supplied_token or not secrets.compare_digest(supplied_token, configured_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal service credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    organization_id = (x_resolveflow_organization_id or "").strip().lower()
    if not ORGANIZATION_ID_PATTERN.fullmatch(organization_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid server-derived organization context is required.",
        )

    request_id = (x_request_id or "").strip()
    if not 8 <= len(request_id) <= 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid request correlation ID is required.",
        )

    # Actor context is optional so that scheduled and system-initiated work can
    # call the service without impersonating a person. When it is present it
    # must be well formed: a malformed actor is a bug, and a bug that silently
    # produces an unattributable action is worse than a failed request.
    membership_id = (x_resolveflow_membership_id or "").strip() or None
    if membership_id is not None and not MEMBERSHIP_ID_PATTERN.fullmatch(membership_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Actor membership context is malformed.",
        )

    role = (x_resolveflow_role or "").strip().upper() or None
    if role is not None and role not in KNOWN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Actor role context is not a recognised role.",
        )

    # Half an actor is not an actor. Either both identify the person, or neither.
    if (membership_id is None) != (role is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Actor membership and role must be supplied together.",
        )

    return InternalContext(
        organization_id=organization_id,
        request_id=request_id,
        actor_membership_id=membership_id,
        actor_role=role,
    )
