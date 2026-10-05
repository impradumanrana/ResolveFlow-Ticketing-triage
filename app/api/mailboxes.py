"""Internal endpoints for connecting and disconnecting a Gmail mailbox.

Called only by the Next.js BFF, which derives the actor from the database on
every request (C03) and forwards nothing from the browser. Three things differ
from the triage endpoints and are worth stating:

* **An actor is mandatory.** Connecting a mailbox is a human decision; system
  calls without membership context are refused rather than attributed to
  nobody.
* **Refusals are returned, not raised.** The service writes an audit event on
  refusal inside the request's database transaction. Raising would propagate
  into the dependency and roll that transaction back, losing the evidence of
  the refused attempt. Returning a 403 response commits it.
* **Configuration fails closed.** With no database, OAuth client, or keyring
  configured, the endpoints answer 503 rather than degrading into anything that
  could hold a token unencrypted.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import JSONResponse

from app.api.contracts import (
    MAILBOX_ID_PATTERN,
    MailboxConnectionCompleteRequest,
    MailboxConnectionOutcomeResponse,
    MailboxConnectionStartResponse,
    RefusalResponse,
)
from app.api.database import _database_url, _engine
from app.api.dependencies import InternalContext, require_internal_context
from app.api.limits import limit_mailbox_complete, limit_mailbox_connect
from app.mailbox.access import Actor
from app.mailbox.connection import ConnectionRefused, ConnectionService
from app.mailbox.google import GoogleOAuthClient, OAuthClientConfig
from app.mailbox.vault import VaultError, vault_from_environment

router = APIRouter(prefix="/v1/mailboxes", tags=["mailboxes"])


def get_connection_service() -> Iterator[ConnectionService]:
    url = _database_url()
    client_id = os.getenv("GMAIL_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GMAIL_OAUTH_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GMAIL_OAUTH_REDIRECT_URI", "").strip()

    if not (url and client_id and client_secret and redirect_uri):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mailbox connection is not configured.",
        )
    try:
        vault = vault_from_environment()
        oauth_config = OAuthClientConfig(client_id, client_secret, redirect_uri)
    except (VaultError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mailbox connection is not configured.",
        ) from error

    from app.mailbox.store import PostgresMailboxStore

    with _engine(url).begin() as connection:
        yield ConnectionService(
            PostgresMailboxStore(connection), GoogleOAuthClient(oauth_config), vault
        )


# Dependencies declared through Annotated rather than as argument defaults: the
# current FastAPI idiom, and it keeps the signatures honest about which values
# the framework supplies.
ContextDep = Annotated[InternalContext, Depends(require_internal_context)]
ServiceDep = Annotated[ConnectionService, Depends(get_connection_service)]
MailboxIdPath = Annotated[str, Path(pattern=MAILBOX_ID_PATTERN)]


def _actor(context: InternalContext) -> Actor | None:
    if not context.actor_membership_id or not context.actor_role:
        return None
    # The BFF only forwards actor context for a membership it resolved as
    # ACTIVE; resolveIdentity refuses every other status before a call is made.
    return Actor(
        membership_id=context.actor_membership_id,
        organization_id=context.organization_id,
        role=context.actor_role,
        status="ACTIVE",
    )


def _refusal(code: str, mailbox_id: str | None = None, http_status: int = 403) -> JSONResponse:
    body = MailboxConnectionOutcomeResponse(ok=False, code=code, mailbox_id=mailbox_id)
    return JSONResponse(status_code=http_status, content=body.model_dump(mode="json"))


@router.post(
    "/{mailbox_id}/connection/start",
    response_model=MailboxConnectionStartResponse,
    responses={
        403: {"model": MailboxConnectionOutcomeResponse},
        429: {"model": RefusalResponse},
    },
    dependencies=[Depends(limit_mailbox_connect)],
)
def start_connection(
    mailbox_id: MailboxIdPath,
    context: ContextDep,
    service: ServiceDep,
) -> MailboxConnectionStartResponse | JSONResponse:
    actor = _actor(context)
    if actor is None:
        return _refusal("ACTOR_REQUIRED", mailbox_id)
    try:
        started = service.start(actor, mailbox_id)
    except ConnectionRefused as refused:
        return _refusal(refused.code, mailbox_id)
    return MailboxConnectionStartResponse(
        mailbox_id=started.mailbox_id,
        attempt_id=started.attempt_id,
        authorization_url=started.authorization_url,
        expires_at=started.expires_at,
    )


@router.post(
    "/connection/complete",
    response_model=MailboxConnectionOutcomeResponse,
    responses={
        403: {"model": MailboxConnectionOutcomeResponse},
        429: {"model": RefusalResponse},
    },
    dependencies=[Depends(limit_mailbox_complete)],
)
def complete_connection(
    payload: MailboxConnectionCompleteRequest,
    context: ContextDep,
    service: ServiceDep,
) -> MailboxConnectionOutcomeResponse | JSONResponse:
    actor = _actor(context)
    if actor is None:
        return _refusal("ACTOR_REQUIRED")
    outcome = service.complete(actor, state=payload.state, code=payload.code)
    if not outcome.ok:
        # 200 with ok=false for provider and policy outcomes the person can act
        # on (wrong account, scope denied); 403 only for authorization failures.
        authorization_codes = {
            "ATTEMPT_NOT_YOURS",
            "ROLE_CANNOT_ADMINISTER_MAILBOXES",
            "MAILBOX_NOT_IN_ORGANIZATION",
            "MEMBERSHIP_NOT_ACTIVE",
        }
        if outcome.code in authorization_codes:
            return _refusal(outcome.code, outcome.mailbox_id)
    return MailboxConnectionOutcomeResponse(
        ok=outcome.ok, code=outcome.code, mailbox_id=outcome.mailbox_id
    )


@router.delete(
    "/{mailbox_id}/connection",
    response_model=MailboxConnectionOutcomeResponse,
    responses={
        403: {"model": MailboxConnectionOutcomeResponse},
        429: {"model": RefusalResponse},
    },
    # Connect and disconnect share an allowance: it is the churn on one
    # mailbox that is worth bounding, in either direction.
    dependencies=[Depends(limit_mailbox_connect)],
)
def revoke_connection(
    mailbox_id: MailboxIdPath,
    context: ContextDep,
    service: ServiceDep,
) -> MailboxConnectionOutcomeResponse | JSONResponse:
    actor = _actor(context)
    if actor is None:
        return _refusal("ACTOR_REQUIRED", mailbox_id)
    outcome = service.revoke(actor, mailbox_id)
    if not outcome.ok and outcome.code == "ROLE_CANNOT_ADMINISTER_MAILBOXES":
        return _refusal(outcome.code, mailbox_id)
    return MailboxConnectionOutcomeResponse(
        ok=outcome.ok, code=outcome.code, mailbox_id=outcome.mailbox_id
    )
