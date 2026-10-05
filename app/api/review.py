"""Internal endpoints for human review (C12).

One endpoint applies a decision; one shows what an approval would create.
Nothing here sends anything, and the preview says so in its own payload.

Refusals use the status that matches the cause, because the web tier shows
different things for each: 404 for a conversation this person may not act on,
403 for a permission or a validation refusal, and 409 for a version conflict -
the one case where the right answer is "look again and decide again".
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import JSONResponse

from app.api.contracts import (
    IDENTIFIER_PATTERN,
    DraftPreviewRequest,
    DraftPreviewResponse,
    ProviderDraftView,
    RefusalResponse,
    ReviewOutcomeResponse,
    ReviewRequestBody,
)
from app.api.dependencies import InternalContext, require_internal_context
from app.api.limits import limit_draft_preview, limit_review
from app.api.mailboxes import _database_url, _engine
from app.mailbox.scopes import ScopeProfile, active_profile
from app.review.drafts import can_create_drafts
from app.review.preview import PreviewRefused
from app.review.records import Actor, Decision, ReviewRefused, ReviewRequest, VersionConflict
from app.review.service import ReviewService

router = APIRouter(prefix="/v1/tickets", tags=["review"])

# Said to the person, not to the log.
MESSAGES: dict[str, str] = {
    "APPLIED": "Recorded.",
    "TICKET_NOT_FOUND": "That conversation is not available to you.",
    "ROLE_LACKS_PERMISSION": "You do not have permission to do that.",
    "ROLE_IS_READ_ONLY": "Your role can read this workspace but not change it.",
    "MEMBERSHIP_NOT_ACTIVE": "Your access to this workspace is not active.",
    "VERSION_CONFLICT": ("Someone else changed this conversation first. Reload and decide again."),
    "REASON_REQUIRED": "Say why, so the next person reading this knows.",
    "BODY_REQUIRED": "There is nothing to save: the reply is empty.",
    "ASSIGNEE_INVALID": "Choose who to assign this to.",
    "REROUTE_TARGET_REQUIRED": "Choose a queue or a department to move this to.",
    "REROUTE_TARGET_INVALID": "That queue or department is not valid.",
    "DRAFT_NOT_GROUNDED": (
        "This answer was not verified against its sources, so it cannot be approved "
        "as it stands. Edit it and approve your own words."
    ),
    "DRAFT_NOT_CITED": "This answer cites nothing, so it cannot be approved as it stands.",
    "NOTHING_TO_APPROVE": "There is no answer to approve.",
    "CONVERSATION_IS_CLOSED": "This conversation is closed.",
    "IDEMPOTENCY_KEY_INVALID": "That request could not be identified. Try again.",
    "BODY_TOO_LONG": "That reply is too long.",
    "REASON_TOO_LONG": "That reason is too long.",
    "VERSION_INVALID": "That request could not be identified. Try again.",
}

DRAFT_MESSAGES: dict[str, str] = {
    "DRAFT_SCOPE_NOT_GRANTED": (
        "No provider draft was created: this mailbox is connected read-only. "
        "The decision is recorded and the conversation is with a person."
    ),
    "NO_CUSTOMER_ADDRESS": (
        "No provider draft was created: there is no customer address to reply to."
    ),
    "MAILBOX_ADDRESS_INVALID": (
        "No provider draft was created: the mailbox address is not usable."
    ),
    "DRAFT_BODY_EMPTY": "No provider draft was created: the reply was empty.",
    "DRAFT_NOT_PERMITTED": "The mail provider refused: this mailbox may not hold drafts.",
    "PROVIDER_RATE_LIMITED": (
        "The mail provider is rate limiting. The decision is recorded; try the draft again shortly."
    ),
    "PROVIDER_UNAVAILABLE": (
        "The mail provider was unavailable. The decision is recorded; try the draft again shortly."
    ),
    "THREAD_NOT_FOUND": "The provider could not find that conversation thread.",
    "DRAFT_OUTCOME_UNKNOWN": (
        "Whether a draft was created is unknown; check the mailbox before approving again."
    ),
    "MAILBOX_TOKEN_UNAVAILABLE": "The mailbox credential could not be used.",
    "DRAFT_REQUEST_REJECTED": "The mail provider rejected the draft.",
    "DRAFT_RESPONSE_INVALID": "The mail provider's answer could not be understood.",
}

STATUS_FOR: dict[str, int] = {
    "TICKET_NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
}


@lru_cache(maxsize=1)
def _drafting_dependencies(url: str) -> tuple[Any, Any] | None:
    """The transport and token provider a draft needs, or None.

    Returns None - and so leaves the review service refusing to create drafts -
    unless all three hold: the deployment runs the `read_and_draft` scope
    profile, the OAuth client is configured, and the vault opens. Any of them
    missing is a deployment that was never authorized to write, so the right
    behaviour is the read-only one, not an error.

    Cached per process because the token provider's cache is the point: a
    dependency rebuilt per request would refresh against Google every time.
    """
    if active_profile() is not ScopeProfile.READ_AND_DRAFT:
        return None

    client_id = os.getenv("GMAIL_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GMAIL_OAUTH_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GMAIL_OAUTH_REDIRECT_URI", "").strip()
    if not (client_id and client_secret and redirect_uri):
        return None

    from app.mailbox.connection import ConnectionService
    from app.mailbox.google import GoogleOAuthClient, HttpxTransport, OAuthClientConfig
    from app.mailbox.store import PostgresMailboxStore
    from app.mailbox.vault import VaultError, vault_from_environment
    from app.review.tokens import RefreshingTokenProvider

    try:
        vault = vault_from_environment()
        config = OAuthClientConfig(client_id, client_secret, redirect_uri)
    except (VaultError, ValueError):
        return None

    engine = _engine(url)

    def refresh(mailbox_id: str) -> Any:
        # Its own short transaction, opened and closed before the Gmail call:
        # the review's transaction is already finished by now, and a slow
        # provider must never hold a row lock (the C10 lesson).
        with engine.begin() as connection:
            service = ConnectionService(
                PostgresMailboxStore(connection), GoogleOAuthClient(config), vault
            )
            return service.refresh(mailbox_id)

    return HttpxTransport(), RefreshingTokenProvider(refresh)


def get_review_service() -> ReviewService:
    url = _database_url()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Review is not configured.",
        )
    from app.review.store import PostgresReviewStore

    store = PostgresReviewStore(_engine(url))
    drafting = _drafting_dependencies(url)
    if drafting is None:
        # No transport and no token provider: this deployment connects
        # mailboxes read-only, so there is nothing to call. The approval is
        # still recorded; the draft is refused with DRAFT_SCOPE_NOT_GRANTED.
        return ReviewService(store)

    transport, tokens = drafting
    return ReviewService(store, transport=transport, token_provider=tokens)


ContextDep = Annotated[InternalContext, Depends(require_internal_context)]
ServiceDep = Annotated[ReviewService, Depends(get_review_service)]
TicketPath = Annotated[str, Path(pattern=IDENTIFIER_PATTERN)]
REFUSALS: dict[int | str, dict[str, Any]] = {
    403: {"model": RefusalResponse},
    404: {"model": RefusalResponse},
    409: {"model": ReviewOutcomeResponse},
    # C13 rate limit. Carries Retry-After; the code says whether the caller
    # was over its allowance or the limiter could not count.
    429: {"model": RefusalResponse},
}


def _actor(context: InternalContext) -> Actor | None:
    if not context.actor_membership_id or not context.actor_role:
        return None
    return Actor(
        membership_id=context.actor_membership_id,
        organization_id=context.organization_id,
        role=context.actor_role,
        status="ACTIVE",
    )


def _message(code: str) -> str:
    return MESSAGES.get(code, "That could not be done.")


def _draft_view(draft: Any) -> ProviderDraftView | None:
    if draft is None:
        return None
    return ProviderDraftView(
        status=draft.status,
        failure_code=draft.failure_code,
        message=(
            "A draft is waiting in the mailbox for a person to review and send."
            if draft.status == "CREATED"
            else DRAFT_MESSAGES.get(draft.failure_code or "", "No provider draft was created.")
        ),
        provider_draft_id=draft.provider_draft_id,
    )


@router.post(
    "/{ticket_id}/review",
    response_model=ReviewOutcomeResponse,
    responses=REFUSALS,
    dependencies=[Depends(limit_review)],
)
def review(
    ticket_id: TicketPath,
    payload: ReviewRequestBody,
    context: ContextDep,
    service: ServiceDep,
) -> ReviewOutcomeResponse | JSONResponse:
    actor = _actor(context)
    if actor is None:
        return JSONResponse(
            status_code=403,
            content=RefusalResponse(code="ACTOR_REQUIRED").model_dump(mode="json"),
        )

    request = ReviewRequest(
        organization_id=context.organization_id,
        ticket_id=ticket_id,
        actor=actor,
        decision=Decision(payload.decision),
        idempotency_key=payload.idempotency_key,
        expected_version=payload.expected_version,
        reason=payload.reason,
        body=payload.body,
        assignee_membership_id=payload.assignee_membership_id,
        queue_id=payload.queue_id,
        department_id=payload.department_id,
    )

    try:
        outcome = service.review(request)
    except VersionConflict as conflict:
        return JSONResponse(
            status_code=409,
            content=ReviewOutcomeResponse(
                ok=False,
                decision=payload.decision,
                code=conflict.code,
                message=_message(conflict.code),
                ticket_version=conflict.current_version,
            ).model_dump(mode="json"),
        )
    except ReviewRefused as refused:
        return JSONResponse(
            status_code=STATUS_FOR.get(refused.code, 403),
            content=RefusalResponse(code=refused.code).model_dump(mode="json"),
        )

    return ReviewOutcomeResponse(
        ok=outcome.ok,
        decision=payload.decision,
        code=outcome.code,
        message=_message(outcome.code),
        ticket_version=outcome.ticket_version,
        revision=outcome.revision,
        replayed=outcome.replayed,
        draft=_draft_view(outcome.draft),
    )


@router.post(
    "/{ticket_id}/draft-preview",
    response_model=DraftPreviewResponse,
    responses=REFUSALS,
    dependencies=[Depends(limit_draft_preview)],
)
def draft_preview(
    ticket_id: TicketPath,
    payload: DraftPreviewRequest,
    context: ContextDep,
    service: ServiceDep,
) -> DraftPreviewResponse | JSONResponse:
    actor = _actor(context)
    if actor is None:
        return JSONResponse(
            status_code=403,
            content=RefusalResponse(code="ACTOR_REQUIRED").model_dump(mode="json"),
        )
    try:
        preview = service.preview(context.organization_id, ticket_id, actor, body=payload.body)
    except ReviewRefused as refused:
        return JSONResponse(
            status_code=STATUS_FOR.get(refused.code, 403),
            content=RefusalResponse(code=refused.code).model_dump(mode="json"),
        )
    except PreviewRefused as refused:
        return JSONResponse(
            status_code=422,
            content=RefusalResponse(code=refused.code).model_dump(mode="json"),
        )

    facts = service.store.load_ticket(
        ReviewRequest(
            organization_id=context.organization_id,
            ticket_id=ticket_id,
            actor=actor,
            decision=Decision.APPROVE,
            idempotency_key="preview-only-not-applied",
            expected_version=0,
        )
    )
    return DraftPreviewResponse(
        from_address=preview.mailbox_address,
        to=list(preview.to),
        cc=list(preview.cc),
        subject=preview.subject,
        body=preview.body,
        in_reply_to=preview.in_reply_to,
        notes=list(preview.notes),
        can_create_draft=bool(facts and can_create_drafts(facts.granted_scopes)),
    )
