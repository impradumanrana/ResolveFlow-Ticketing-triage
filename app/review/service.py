"""The one way a person decides anything about a conversation.

Order matters, and it is the same for every decision:

1. **Replay.** An idempotency key that has been seen returns what happened the
   first time. A reviewer double-clicking must not approve twice.
2. **Visibility.** The store loads the conversation only if this person may act
   on it. An invisible conversation answers "not found", exactly as C08's pages
   do, because whether a ticket exists in a mailbox you cannot see is itself
   information.
3. **Permission**, then **validation**, then the change under optimistic
   locking, all recorded with the actor, the reason, and the versions.
4. **The provider draft**, only for an approval, only after the decision is
   committed, and only if the mailbox was actually granted the scope to hold a
   draft. There is no send call in this package.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.review.access import refusal_for
from app.review.drafts import DraftRefused, RefusingComposer, composer_for
from app.review.preview import DraftPreview, InboundMessage, PreviewRefused, build_preview
from app.review.records import (
    DECISIONS_REQUIRING_A_REASON,
    MAX_BODY_CHARACTERS,
    MAX_IDEMPOTENCY_KEY,
    MAX_REASON_CHARACTERS,
    MIN_IDEMPOTENCY_KEY,
    Actor,
    Decision,
    DraftOutcome,
    ReviewOutcome,
    ReviewRefused,
    ReviewRequest,
    ReviewStore,
    TicketFacts,
    VersionConflict,
)

IDENTIFIER = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
STALE_CLAIM = timedelta(minutes=10)


class ReviewService:
    def __init__(
        self,
        store: ReviewStore,
        *,
        transport: Any | None = None,
        token_provider: Callable[[str], str] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.store = store
        self.transport = transport
        self.token_provider = token_provider
        self.now = now

    # ------------------------------------------------------------------

    def review(self, request: ReviewRequest) -> ReviewOutcome:
        _check_shape(request)

        replay = self.store.find_replay(request.organization_id, request.idempotency_key)
        if replay is not None:
            return replay

        facts = self.store.load_ticket(request)
        if facts is None:
            # No action row: it would have to reference a ticket this person
            # may not know about. The attempt is audited by the store.
            raise ReviewRefused("TICKET_NOT_FOUND")

        if facts.version != request.expected_version:
            # Checked before anything else: every other refusal could be an
            # artefact of a view that has since changed, and "reload and decide
            # again" is the only answer that helps. The store re-checks the
            # version atomically for the race between here and the update.
            self.store.record_refusal(
                request, "VERSION_CONFLICT", ticket_version=facts.version, at=self.now()
            )
            raise VersionConflict(facts.version)

        refusal = refusal_for(request.actor.role, request.actor.status, request.decision)
        if refusal is None:
            refusal = _check_decision(request, facts)
        if refusal is not None:
            self.store.record_refusal(request, refusal, ticket_version=facts.version, at=self.now())
            raise ReviewRefused(refusal, ticket_version=facts.version)

        try:
            outcome = self.store.apply(request, facts, at=self.now())
        except VersionConflict as conflict:
            # Recorded, because "two people decided at once" is something an
            # operator needs to be able to see afterwards.
            self.store.record_refusal(
                request, conflict.code, ticket_version=conflict.current_version, at=self.now()
            )
            raise

        if request.decision is Decision.APPROVE:
            outcome = self._create_provider_draft(request, facts, outcome)
        return outcome

    def preview(
        self, organization_id: str, ticket_id: str, actor: Actor, *, body: str | None = None
    ) -> DraftPreview:
        """What an approval would create. Read-only."""
        request = ReviewRequest(
            organization_id=organization_id,
            ticket_id=ticket_id,
            actor=actor,
            decision=Decision.APPROVE,
            idempotency_key="preview-only-not-applied",
            expected_version=0,
        )
        facts = self.store.load_ticket(request)
        if facts is None:
            raise ReviewRefused("TICKET_NOT_FOUND")
        return self._build_preview(facts, body or facts.latest_body or facts.model_draft or "")

    def release_stale_claims(self) -> int:
        at = self.now()
        return self.store.release_stale_claims(at - STALE_CLAIM, at=at)

    # ------------------------------------------------------------------

    def _build_preview(self, facts: TicketFacts, body: str) -> DraftPreview:
        inbound = None
        row = self.store.inbound_message(facts.organization_id, facts.thread_id)
        if row:
            inbound = InboundMessage(
                from_address=str(row.get("from_address") or ""),
                subject=row.get("subject") or facts.subject,
                rfc822_message_id=row.get("rfc822_message_id"),
                to_addresses=tuple(row.get("to_addresses") or ()),
                cc_addresses=tuple(row.get("cc_addresses") or ()),
            )
        return build_preview(
            mailbox_address=facts.mailbox_address,
            thread_id=facts.thread_id,
            body=body,
            inbound=inbound,
            customer_address=facts.customer_address,
        )

    def _create_provider_draft(
        self, request: ReviewRequest, facts: TicketFacts, outcome: ReviewOutcome
    ) -> ReviewOutcome:
        body = (request.body or facts.latest_body or facts.model_draft or "").strip()
        claimed = self.store.claim_provider_draft(
            request,
            facts,
            body=body,
            revision_id=outcome.revision_id,
            revision=outcome.revision,
            at=self.now(),
        )

        result = self._compose(facts, body)
        self.store.settle_provider_draft(
            request.organization_id, claimed.draft_id, result, at=self.now()
        )
        record = getattr(self.store, "record_draft_action", None)
        if callable(record):
            record(request, facts, result, at=self.now())
        attach = getattr(self.store, "attach_draft_outcome", None)
        if callable(attach) and outcome.action_id:
            attach(request.organization_id, outcome.action_id, result)

        from dataclasses import replace

        return replace(
            outcome,
            draft=result,
            revision=claimed.revision or outcome.revision,
            revision_id=claimed.revision_id or outcome.revision_id,
        )

    def _compose(self, facts: TicketFacts, body: str) -> DraftOutcome:
        """Build the draft and create it, or say exactly why it was not created."""
        composer = composer_for(facts.granted_scopes, self.transport)
        if isinstance(composer, RefusingComposer):
            # Said first, and on its own: a mailbox that cannot hold a draft
            # cannot hold one whatever the reply looks like, and the reason an
            # operator needs is the scope, not the address.
            return DraftOutcome(status="REFUSED", failure_code=composer.code)

        try:
            preview = self._build_preview(facts, body)
        except PreviewRefused as refused:
            return DraftOutcome(status="REFUSED", failure_code=refused.code)

        if self.token_provider is None:
            # A transport with no way to authenticate would call Gmail with an
            # empty bearer and report a 401 as if the mailbox were at fault.
            return DraftOutcome(status="FAILED", failure_code="MAILBOX_TOKEN_UNAVAILABLE")
        try:
            token = self.token_provider(facts.mailbox_id)
        except Exception:
            # The code C06 gave is already recorded against the mailbox; this
            # one says only that the draft could not be authenticated.
            return DraftOutcome(status="FAILED", failure_code="MAILBOX_TOKEN_UNAVAILABLE")
        if not token:
            return DraftOutcome(status="FAILED", failure_code="MAILBOX_TOKEN_UNAVAILABLE")

        failure: DraftRefused | None = None
        created = None
        try:
            created = composer.create(token, raw=preview.raw(), thread_id=preview.thread_id)
        except DraftRefused as refused:
            failure = refused
        except Exception:
            failure = DraftRefused("PROVIDER_UNAVAILABLE", transient=True)

        if failure is not None:
            status = "REFUSED" if failure.code == "DRAFT_SCOPE_NOT_GRANTED" else "FAILED"
            return DraftOutcome(status=status, failure_code=failure.code)
        assert created is not None
        return DraftOutcome(
            status="CREATED",
            provider_draft_id=created.provider_draft_id,
            provider_thread_id=created.provider_thread_id,
        )


# ---------------------------------------------------------------------------
# Validation. Pure, so every refusal is testable without a store.
# ---------------------------------------------------------------------------


def _check_shape(request: ReviewRequest) -> None:
    if not MIN_IDEMPOTENCY_KEY <= len(request.idempotency_key) <= MAX_IDEMPOTENCY_KEY:
        raise ReviewRefused("IDEMPOTENCY_KEY_INVALID")
    if request.expected_version < 0:
        raise ReviewRefused("VERSION_INVALID")
    if request.reason is not None and len(request.reason) > MAX_REASON_CHARACTERS:
        raise ReviewRefused("REASON_TOO_LONG")
    if request.body is not None and len(request.body) > MAX_BODY_CHARACTERS:
        raise ReviewRefused("BODY_TOO_LONG")


def _check_decision(request: ReviewRequest, facts: TicketFacts) -> str | None:
    decision = request.decision

    if decision in DECISIONS_REQUIRING_A_REASON and not (request.reason or "").strip():
        return "REASON_REQUIRED"

    if decision is Decision.EDIT and not (request.body or "").strip():
        return "BODY_REQUIRED"

    if decision is Decision.ASSIGN and not IDENTIFIER.fullmatch(
        request.assignee_membership_id or ""
    ):
        return "ASSIGNEE_INVALID"

    if decision is Decision.REROUTE:
        if not (request.queue_id or request.department_id):
            return "REROUTE_TARGET_REQUIRED"
        for value in (request.queue_id, request.department_id):
            if value is not None and not IDENTIFIER.fullmatch(value):
                return "REROUTE_TARGET_INVALID"

    if decision is Decision.APPROVE:
        body = (request.body or facts.latest_body or facts.model_draft or "").strip()
        if not body:
            return "NOTHING_TO_APPROVE"
        edited = bool(request.body and request.body.strip() != (facts.model_draft or "").strip())
        human_revision = bool(facts.latest_revision and facts.latest_revision > 1)
        if not edited and not human_revision:
            # Approving the model's own words requires the grounding the
            # validator checked (C-D017); a person's own words are their own
            # accountability.
            if not facts.grounding_validated:
                return "DRAFT_NOT_GROUNDED"
            if not facts.citations:
                return "DRAFT_NOT_CITED"

    if facts.status in ("RESOLVED", "CLOSED") and decision is not Decision.ASSIGN:
        return "CONVERSATION_IS_CLOSED"

    return None
