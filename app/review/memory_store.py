"""In-memory review store for offline tests.

Mirrors the PostgreSQL store's guarantees: visibility by mailbox permission and
department, optimistic locking on the ticket version, idempotent replay, and a
provider draft claimed before the provider is called - including the unique
live-draft-per-ticket rule the database enforces with a partial index.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from app.mailbox.access import ORGANIZATION_WIDE_ROLES
from app.review.records import (
    ACTION_TYPES,
    ClaimedDraft,
    Decision,
    DraftOutcome,
    ReviewOutcome,
    ReviewRequest,
    TicketFacts,
    VersionConflict,
    digest,
)
from app.review.store import STATUS_AFTER


@dataclass
class InMemoryReviewStore:
    tickets: dict[str, TicketFacts] = field(default_factory=dict)
    # membership -> mailboxes they may act in, and departments they belong to
    action_permissions: dict[str, set[str]] = field(default_factory=dict)
    department_memberships: dict[str, set[str]] = field(default_factory=dict)
    inbound: dict[str, dict[str, Any]] = field(default_factory=dict)
    mailbox_addresses: dict[str, str] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    audits: list[dict[str, Any]] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    drafts: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    # -- reads ----------------------------------------------------------

    def _visible(self, request: ReviewRequest, facts: TicketFacts) -> bool:
        if facts.organization_id != request.organization_id:
            return False
        if request.actor.role in ORGANIZATION_WIDE_ROLES:
            return True
        if facts.mailbox_id not in self.action_permissions.get(request.actor.membership_id, set()):
            return False
        if facts.department_id and facts.department_id not in self.department_memberships.get(
            request.actor.membership_id, set()
        ):
            return False
        return True

    def load_ticket(self, request: ReviewRequest) -> TicketFacts | None:
        facts = self.tickets.get(request.ticket_id)
        if facts is None or not self._visible(request, facts):
            return None
        # The database joins the address into the same row; so does this.
        address = self.mailbox_addresses.get(facts.mailbox_id, facts.mailbox_address)
        return replace(facts, mailbox_address=address)

    def inbound_message(self, organization_id: str, thread_id: str) -> dict[str, Any] | None:
        return self.inbound.get(thread_id)

    def mailbox_address(self, mailbox_id: str) -> str:
        return self.mailbox_addresses.get(mailbox_id, "")

    def find_replay(self, organization_id: str, idempotency_key: str) -> ReviewOutcome | None:
        for action in self.actions:
            if (
                action["organization_id"] == organization_id
                and action.get("idempotency_key") == idempotency_key
            ):
                payload = action["payload"]
                draft = payload.get("draft")
                return ReviewOutcome(
                    ok=action["outcome"] == "SUCCEEDED",
                    decision=Decision(payload["decision"]),
                    code=action.get("error_code") or payload.get("code", "APPLIED"),
                    ticket_version=int(action.get("ticket_version_after") or 0),
                    action_id=action["id"],
                    revision=payload.get("revision"),
                    revision_id=payload.get("revision_id"),
                    draft=DraftOutcome(**draft) if draft else None,
                    replayed=True,
                )
        return None

    # -- writes ---------------------------------------------------------

    def record_refusal(
        self, request: ReviewRequest, code: str, *, ticket_version: int | None, at: datetime
    ) -> None:
        self.actions.append(
            {
                "id": str(uuid.uuid4()),
                "organization_id": request.organization_id,
                "ticket_id": request.ticket_id,
                "actor_membership_id": request.actor.membership_id,
                "action_type": ACTION_TYPES[request.decision],
                "idempotency_key": None,
                "reason": request.reason,
                "outcome": "REJECTED",
                "error_code": code,
                "ticket_version_before": ticket_version,
                "ticket_version_after": ticket_version,
                "payload": {"decision": request.decision.value, "code": code},
                "occurred_at": at,
            }
        )
        self.audits.append(
            {
                "action": f"review.{request.decision.value.lower()}",
                "outcome": "DENIED",
                "reason_code": code,
                "actor_membership_id": request.actor.membership_id,
                "target_id": request.ticket_id,
            }
        )

    def apply(self, request: ReviewRequest, facts: TicketFacts, *, at: datetime) -> ReviewOutcome:
        with self._lock:
            current = self.tickets[facts.ticket_id]
            if current.version != request.expected_version:
                raise VersionConflict(current.version)

            revision_id = current.latest_revision_id
            revision_number = current.latest_revision or None
            if (
                request.body is not None
                and request.body.strip() != (current.latest_body or "").strip()
            ):
                revision_number = (current.latest_revision or 0) + 1
                revision_id = str(uuid.uuid4())
                self.revisions.append(
                    {
                        "id": revision_id,
                        "ticket_id": facts.ticket_id,
                        "revision": revision_number,
                        "source": "HUMAN",
                        "body": request.body.strip(),
                        "body_sha256": digest(request.body.strip()),
                        "created_by_membership_id": request.actor.membership_id,
                    }
                )

            status = STATUS_AFTER[request.decision] or current.status
            updated = replace(
                current,
                version=current.version + 1,
                status=status,
                latest_revision=revision_number or 0,
                latest_revision_id=revision_id,
                latest_body=request.body.strip() if request.body else current.latest_body,
                assigned_membership_id=(
                    request.assignee_membership_id
                    if request.decision is Decision.ASSIGN
                    else current.assigned_membership_id
                ),
                queue_id=(
                    request.queue_id or current.queue_id
                    if request.decision is Decision.REROUTE
                    else current.queue_id
                ),
                department_id=(
                    request.department_id or current.department_id
                    if request.decision is Decision.REROUTE
                    else current.department_id
                ),
            )
            self.tickets[facts.ticket_id] = updated

            payload: dict[str, Any] = {
                "decision": request.decision.value,
                "code": "APPLIED",
                "ticket_version": updated.version,
                "revision": revision_number,
                "revision_id": revision_id,
            }
            if request.decision is Decision.ASSIGN:
                payload["assignee_membership_id"] = request.assignee_membership_id
            if request.decision is Decision.REROUTE:
                payload["queue_id"] = request.queue_id
                payload["department_id"] = request.department_id
            action_id = str(uuid.uuid4())
            self.actions.append(
                {
                    "id": action_id,
                    "organization_id": request.organization_id,
                    "ticket_id": facts.ticket_id,
                    "actor_membership_id": request.actor.membership_id,
                    "action_type": ACTION_TYPES[request.decision],
                    "idempotency_key": request.idempotency_key,
                    "reason": request.reason,
                    "outcome": "SUCCEEDED",
                    "error_code": None,
                    "ticket_version_before": request.expected_version,
                    "ticket_version_after": updated.version,
                    "payload": payload,
                    "occurred_at": at,
                }
            )
            self.audits.append(
                {
                    "action": f"review.{request.decision.value.lower()}",
                    "outcome": "ALLOWED",
                    "reason_code": None,
                    "actor_membership_id": request.actor.membership_id,
                    "target_id": facts.ticket_id,
                }
            )
            return ReviewOutcome(
                ok=True,
                decision=request.decision,
                code="APPLIED",
                ticket_version=updated.version,
                action_id=action_id,
                revision=revision_number,
                revision_id=revision_id,
            )

    def claim_provider_draft(
        self,
        request: ReviewRequest,
        facts: TicketFacts,
        *,
        body: str,
        revision_id: str | None = None,
        revision: int | None = None,
        at: datetime,
    ) -> ClaimedDraft:
        with self._lock:
            key = f"{request.idempotency_key}:draft"
            if any(draft["idempotency_key"] == key for draft in self.drafts):
                raise AssertionError("a draft was claimed twice for one key")
            if any(
                draft["ticket_id"] == facts.ticket_id and draft["status"] in ("PENDING", "CREATED")
                for draft in self.drafts
            ):
                raise AssertionError("a second live draft was claimed for one ticket")

            current = self.tickets[facts.ticket_id]
            # Mirrors the database: the caller names the revision the decision
            # produced, and only a missing one is created here.
            # Falls back to the snapshot the caller passed, exactly as the
            # PostgreSQL store does - not to the current row, which would
            # quietly repair a caller that passed the wrong revision.
            revision_id = revision_id or facts.latest_revision_id
            revision = revision or facts.latest_revision
            if revision_id is None and any(
                row["ticket_id"] == facts.ticket_id for row in self.revisions
            ):
                # The database's unique (ticket_id, revision) index would raise
                # here. A store that quietly repaired it would hide the bug.
                raise AssertionError("revision 1 would be written twice")
            if revision_id is None:
                revision = 1
                revision_id = str(uuid.uuid4())
                self.revisions.append(
                    {
                        "id": revision_id,
                        "ticket_id": facts.ticket_id,
                        "revision": 1,
                        "source": "MODEL",
                        "body": body,
                        "body_sha256": digest(body),
                        "created_by_membership_id": None,
                    }
                )
                self.tickets[facts.ticket_id] = replace(
                    current, latest_revision=1, latest_revision_id=revision_id, latest_body=body
                )

            draft_id = str(uuid.uuid4())
            self.drafts.append(
                {
                    "id": draft_id,
                    "organization_id": request.organization_id,
                    "ticket_id": facts.ticket_id,
                    "mailbox_id": facts.mailbox_id,
                    "revision_id": revision_id,
                    "status": "PENDING",
                    "failure_code": None,
                    "provider_draft_id": None,
                    "provider_thread_id": None,
                    "approved_by_membership_id": request.actor.membership_id,
                    "idempotency_key": key,
                    "body_sha256": digest(body),
                    "claimed_at": at,
                    "settled_at": None,
                }
            )
            return ClaimedDraft(
                draft_id=draft_id, revision_id=revision_id, revision=revision or 1, body=body
            )

    def settle_provider_draft(
        self, organization_id: str, draft_id: str, outcome: DraftOutcome, *, at: datetime
    ) -> None:
        for draft in self.drafts:
            if draft["id"] == draft_id and draft["status"] == "PENDING":
                draft.update(
                    status=outcome.status,
                    failure_code=outcome.failure_code,
                    provider_draft_id=outcome.provider_draft_id,
                    provider_thread_id=outcome.provider_thread_id,
                    settled_at=at,
                )

    def record_draft_action(
        self, request: ReviewRequest, facts: TicketFacts, outcome: DraftOutcome, *, at: datetime
    ) -> None:
        self.actions.append(
            {
                "id": str(uuid.uuid4()),
                "organization_id": request.organization_id,
                "ticket_id": facts.ticket_id,
                "actor_membership_id": request.actor.membership_id,
                "action_type": "PROVIDER_DRAFT_CREATED",
                "idempotency_key": f"{request.idempotency_key}:draft-action",
                "reason": request.reason,
                "outcome": "SUCCEEDED" if outcome.status == "CREATED" else "FAILED",
                "error_code": outcome.failure_code,
                "ticket_version_before": None,
                "ticket_version_after": None,
                "payload": {
                    "provider": "gmail",
                    "status": outcome.status,
                    "provider_draft_id": outcome.provider_draft_id,
                },
                "occurred_at": at,
            }
        )

    def attach_draft_outcome(
        self, organization_id: str, action_id: str, outcome: DraftOutcome
    ) -> None:
        for action in self.actions:
            if action["id"] == action_id:
                action["payload"]["draft"] = {
                    "status": outcome.status,
                    "failure_code": outcome.failure_code,
                    "provider_draft_id": outcome.provider_draft_id,
                    "provider_thread_id": outcome.provider_thread_id,
                }

    def release_stale_claims(self, before: datetime, *, at: datetime) -> int:
        released = 0
        for draft in self.drafts:
            if draft["status"] == "PENDING" and draft["claimed_at"] < before:
                draft.update(status="FAILED", failure_code="DRAFT_OUTCOME_UNKNOWN", settled_at=at)
                released += 1
        return released
