"""What a review decision is, and what it produces."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

MAX_BODY_CHARACTERS = 20000
MAX_REASON_CHARACTERS = 2000
MIN_IDEMPOTENCY_KEY = 8
MAX_IDEMPOTENCY_KEY = 200


class Decision(StrEnum):
    EDIT = "EDIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REROUTE = "REROUTE"
    ASSIGN = "ASSIGN"
    RESOLVE = "RESOLVE"


# The C04 action type each decision is recorded as.
ACTION_TYPES: dict[Decision, str] = {
    Decision.EDIT: "DRAFT_EDITED",
    Decision.APPROVE: "DRAFT_APPROVED",
    Decision.REJECT: "DRAFT_REJECTED",
    Decision.REROUTE: "TICKET_REROUTED",
    Decision.ASSIGN: "TICKET_ASSIGNED",
    Decision.RESOLVE: "TICKET_RESOLVED",
}

# A decision that changes where work goes, or says no, owes an explanation.
DECISIONS_REQUIRING_A_REASON = frozenset({Decision.REJECT, Decision.REROUTE})


def digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Actor:
    membership_id: str
    organization_id: str
    role: str
    status: str = "ACTIVE"


@dataclass(frozen=True)
class ReviewRequest:
    organization_id: str
    ticket_id: str
    actor: Actor
    decision: Decision
    idempotency_key: str
    # The version the person saw. A decision made against a stale view is
    # refused rather than applied to something they did not look at.
    expected_version: int
    reason: str | None = None
    body: str | None = None
    assignee_membership_id: str | None = None
    queue_id: str | None = None
    department_id: str | None = None


@dataclass(frozen=True)
class TicketFacts:
    ticket_id: str
    organization_id: str
    mailbox_id: str
    thread_id: str
    version: int
    status: str
    mailbox_address: str = ""
    reference: int | None = None
    subject: str | None = None
    customer_address: str | None = None
    queue_id: str | None = None
    department_id: str | None = None
    assigned_membership_id: str | None = None
    triage_run_id: str | None = None
    model_draft: str | None = None
    citations: tuple[str, ...] = ()
    grounding_validated: bool = False
    route: str | None = None
    latest_revision: int = 0
    latest_revision_id: str | None = None
    latest_body: str | None = None
    granted_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftOutcome:
    status: str  # CREATED, FAILED, REFUSED
    failure_code: str | None = None
    provider_draft_id: str | None = None
    provider_thread_id: str | None = None


@dataclass(frozen=True)
class ReviewOutcome:
    ok: bool
    decision: Decision
    code: str
    ticket_version: int
    action_id: str | None = None
    revision: int | None = None
    revision_id: str | None = None
    draft: DraftOutcome | None = None
    replayed: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def provider_draft_created(self) -> bool:
        return bool(self.draft and self.draft.status == "CREATED")


class ReviewRefused(Exception):
    """The decision was not applied, and the refusal is recorded."""

    def __init__(self, code: str, *, ticket_version: int | None = None):
        super().__init__(code)
        self.code = code
        self.ticket_version = ticket_version


class VersionConflict(ReviewRefused):
    """Someone else changed the conversation first."""

    def __init__(self, current_version: int):
        super().__init__("VERSION_CONFLICT", ticket_version=current_version)
        self.current_version = current_version


@dataclass(frozen=True)
class ClaimedDraft:
    draft_id: str
    revision_id: str
    revision: int
    body: str


class ReviewStore(Protocol):
    def load_ticket(self, request: ReviewRequest) -> TicketFacts | None:
        """The ticket as this actor may see it, or None."""
        ...

    def inbound_message(self, organization_id: str, thread_id: str) -> dict[str, Any] | None:
        """The latest inbound message, for addressing the reply."""
        ...

    def find_replay(self, organization_id: str, idempotency_key: str) -> ReviewOutcome | None: ...

    def record_refusal(
        self, request: ReviewRequest, code: str, *, ticket_version: int | None, at: datetime
    ) -> None: ...

    def apply(self, request: ReviewRequest, facts: TicketFacts, *, at: datetime) -> ReviewOutcome:
        """Apply the decision under optimistic locking, in one transaction."""
        ...

    def claim_provider_draft(
        self,
        request: ReviewRequest,
        facts: TicketFacts,
        *,
        body: str,
        revision_id: str | None,
        revision: int | None,
        at: datetime,
    ) -> ClaimedDraft:
        """Reserve one draft against the revision the decision just produced.

        The revision is passed in rather than read from `facts`: those were
        loaded before the decision was applied, and using them would try to
        write revision 1 twice.
        """
        ...

    def settle_provider_draft(
        self, organization_id: str, draft_id: str, outcome: DraftOutcome, *, at: datetime
    ) -> None: ...

    def release_stale_claims(self, before: datetime, *, at: datetime) -> int: ...
