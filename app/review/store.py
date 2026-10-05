"""Where a review decision is written.

Three properties are the store's job, not the caller's:

* **Visibility.** A decision can only touch a conversation the actor may act
  on: their organization, a mailbox they may act in, and a department they
  belong to - unless their role is organization-wide. Acting needs
  `can_action`, not the `can_view` the inbox list needs (C-D028, C06).
* **Optimistic locking.** The ticket update carries the version the person saw.
  Zero rows updated means someone else was first, and that is reported, not
  retried.
* **One transaction.** The revision, the ticket change, the action row, and the
  audit event are one decision.

The provider draft is deliberately *not* in that transaction: it is claimed,
committed, then created at the provider, then settled. Holding a transaction
open across a provider call would lock the ticket for the length of a network
round trip (C-D101).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import text

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

# Acting on a conversation, not merely seeing it.
VISIBILITY = """
    t.organization_id = CAST(:org AS uuid)
    AND t.deleted_at IS NULL
    AND (
        :org_wide
        OR EXISTS (
            SELECT 1 FROM mailbox_permissions mp
             WHERE mp.mailbox_id = t.mailbox_id
               AND mp.membership_id = CAST(:membership AS uuid)
               AND mp.can_action
        )
    )
    AND (
        :org_wide
        OR t.department_id IS NULL
        OR EXISTS (
            SELECT 1 FROM department_memberships dm
             WHERE dm.department_id = t.department_id
               AND dm.membership_id = CAST(:membership AS uuid)
        )
    )
"""

# What each decision does to the conversation's status.
STATUS_AFTER: dict[Decision, str | None] = {
    Decision.EDIT: None,
    Decision.APPROVE: "IN_PROGRESS",
    Decision.REJECT: "IN_PROGRESS",
    Decision.REROUTE: None,
    Decision.ASSIGN: None,
    Decision.RESOLVE: "RESOLVED",
}


class PostgresReviewStore:
    def __init__(self, engine: Any):
        self._engine = engine

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        with self._engine.begin() as connection:
            yield connection

    def _scope(self, request: ReviewRequest) -> dict[str, Any]:
        return {
            "org": request.organization_id,
            "membership": request.actor.membership_id,
            "org_wide": request.actor.role in ORGANIZATION_WIDE_ROLES,
        }

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def load_ticket(self, request: ReviewRequest) -> TicketFacts | None:
        with self._tx() as c:
            row = c.execute(
                text(
                    "SELECT t.id::text AS ticket_id, t.organization_id::text AS organization_id, "
                    "t.mailbox_id::text AS mailbox_id, t.thread_id::text AS thread_id, t.version, "
                    "t.status::text AS status, t.reference, t.subject, t.customer_address, "
                    "mb.email_address AS mailbox_address, "
                    "t.queue_id::text AS queue_id, t.department_id::text AS department_id, "
                    "t.assigned_membership_id::text AS assigned_membership_id, "
                    "t.route::text AS route, "
                    "run.id::text AS triage_run_id, run.draft AS model_draft, run.citations, "
                    "run.grounding_validated, "
                    "rev.id::text AS latest_revision_id, rev.revision AS latest_revision, "
                    "rev.body AS latest_body, "
                    "COALESCE(cred.granted_scopes, '{}'::text[]) AS granted_scopes "
                    "FROM tickets t "
                    "JOIN mailboxes mb ON mb.id = t.mailbox_id "
                    "LEFT JOIN LATERAL ("
                    "  SELECT id, draft, citations, grounding_validated FROM triage_runs "
                    "   WHERE ticket_id = t.id AND deleted_at IS NULL "
                    "   ORDER BY created_at DESC LIMIT 1) run ON true "
                    "LEFT JOIN LATERAL ("
                    "  SELECT id, revision, body FROM draft_revisions "
                    "   WHERE ticket_id = t.id ORDER BY revision DESC LIMIT 1) rev ON true "
                    "LEFT JOIN mailbox_credentials cred ON cred.mailbox_id = t.mailbox_id "
                    f"WHERE t.id = CAST(:ticket AS uuid) AND {VISIBILITY}",
                ),
                {**self._scope(request), "ticket": request.ticket_id},
            ).first()
        if row is None:
            return None
        return TicketFacts(
            ticket_id=row.ticket_id,
            organization_id=row.organization_id,
            mailbox_id=row.mailbox_id,
            thread_id=row.thread_id,
            version=int(row.version),
            status=row.status,
            mailbox_address=row.mailbox_address or "",
            reference=row.reference,
            subject=row.subject,
            customer_address=row.customer_address,
            queue_id=row.queue_id,
            department_id=row.department_id,
            assigned_membership_id=row.assigned_membership_id,
            triage_run_id=row.triage_run_id,
            model_draft=row.model_draft,
            citations=tuple(row.citations or ()),
            grounding_validated=bool(row.grounding_validated),
            route=row.route,
            latest_revision=int(row.latest_revision or 0),
            latest_revision_id=row.latest_revision_id,
            latest_body=row.latest_body,
            granted_scopes=tuple(row.granted_scopes or ()),
        )

    def inbound_message(self, organization_id: str, thread_id: str) -> dict[str, Any] | None:
        """The latest inbound message, for addressing the reply."""
        with self._tx() as c:
            row = c.execute(
                text(
                    "SELECT from_address, subject, rfc822_message_id, to_addresses, cc_addresses "
                    "FROM messages WHERE organization_id = CAST(:org AS uuid) "
                    "AND thread_id = CAST(:thread AS uuid) AND direction = 'INBOUND' "
                    "ORDER BY sent_at DESC LIMIT 1"
                ),
                {"org": organization_id, "thread": thread_id},
            ).first()
        return dict(row._mapping) if row is not None else None

    def find_replay(self, organization_id: str, idempotency_key: str) -> ReviewOutcome | None:
        with self._tx() as c:
            row = c.execute(
                text(
                    "SELECT id::text AS id, action_type::text AS action_type, outcome, "
                    "error_code, ticket_version_after, payload FROM actions "
                    "WHERE organization_id = CAST(:org AS uuid) AND idempotency_key = :key"
                ),
                {"org": organization_id, "key": idempotency_key},
            ).first()
        if row is None:
            return None
        payload = row.payload or {}
        draft = payload.get("draft") or None
        return ReviewOutcome(
            ok=row.outcome == "SUCCEEDED",
            decision=Decision(payload.get("decision", Decision.EDIT.value)),
            code=row.error_code or payload.get("code", "APPLIED"),
            ticket_version=int(row.ticket_version_after or payload.get("ticket_version", 0)),
            action_id=row.id,
            revision=payload.get("revision"),
            revision_id=payload.get("revision_id"),
            draft=DraftOutcome(**draft) if draft else None,
            replayed=True,
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record_refusal(
        self, request: ReviewRequest, code: str, *, ticket_version: int | None, at: datetime
    ) -> None:
        """A refused decision is still a decision somebody made."""
        with self._tx() as c:
            self._insert_action(
                c,
                request,
                outcome="REJECTED",
                error_code=code,
                version_before=ticket_version,
                version_after=ticket_version,
                payload={"decision": request.decision.value, "code": code},
                at=at,
                idempotency_key=None,
            )
            self._audit(
                c,
                request,
                action=f"review.{request.decision.value.lower()}",
                outcome="DENIED",
                reason_code=code,
            )

    def apply(self, request: ReviewRequest, facts: TicketFacts, *, at: datetime) -> ReviewOutcome:
        decision = request.decision
        with self._tx() as c:
            revision_id: str | None = facts.latest_revision_id
            revision_number: int | None = facts.latest_revision or None

            if (
                request.body is not None
                and request.body.strip() != (facts.latest_body or "").strip()
            ):
                revision_number = (facts.latest_revision or 0) + 1
                revision_id = self._insert_revision(
                    c,
                    request,
                    facts,
                    body=request.body.strip(),
                    source="HUMAN",
                    revision=revision_number,
                    at=at,
                )

            version_after = self._update_ticket(c, request, facts, at=at)

            payload: dict[str, Any] = {
                "decision": decision.value,
                "code": "APPLIED",
                "ticket_version": version_after,
                "revision": revision_number,
                "revision_id": revision_id,
            }
            if decision is Decision.ASSIGN:
                payload["assignee_membership_id"] = request.assignee_membership_id
            if decision is Decision.REROUTE:
                payload["queue_id"] = request.queue_id
                payload["department_id"] = request.department_id

            action_id = self._insert_action(
                c,
                request,
                outcome="SUCCEEDED",
                error_code=None,
                version_before=facts.version,
                version_after=version_after,
                payload=payload,
                at=at,
                idempotency_key=request.idempotency_key,
            )
            self._audit(
                c,
                request,
                action=f"review.{decision.value.lower()}",
                outcome="ALLOWED",
                reason_code=None,
            )
        return ReviewOutcome(
            ok=True,
            decision=decision,
            code="APPLIED",
            ticket_version=version_after,
            action_id=action_id,
            revision=revision_number,
            revision_id=revision_id,
        )

    def ensure_model_revision(
        self, request: ReviewRequest, facts: TicketFacts, *, at: datetime
    ) -> ClaimedDraft:
        """Record the model's own candidate as revision 1 before it is approved.

        Stored rather than read from the triage run at approval time, so the
        text that was approved cannot change afterwards.
        """
        body = (facts.latest_body or facts.model_draft or "").strip()
        if facts.latest_revision_id and facts.latest_body:
            return ClaimedDraft(
                draft_id="",
                revision_id=facts.latest_revision_id,
                revision=facts.latest_revision,
                body=facts.latest_body.strip(),
            )
        with self._tx() as c:
            revision_id = self._insert_revision(
                c, request, facts, body=body, source="MODEL", revision=1, at=at
            )
        return ClaimedDraft(draft_id="", revision_id=revision_id, revision=1, body=body)

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
        """Reserve the right to create one draft, before calling the provider.

        `revision_id` is the one the decision just wrote. Falling back to the
        facts loaded before the decision would insert revision 1 a second time.
        """
        revision_id = revision_id or facts.latest_revision_id
        revision = revision or facts.latest_revision
        with self._tx() as c:
            if revision_id is None:
                revision = 1
                revision_id = self._insert_revision(
                    c, request, facts, body=body, source="MODEL", revision=1, at=at
                )
            draft_id = c.execute(
                text(
                    "INSERT INTO provider_drafts (organization_id, ticket_id, mailbox_id, "
                    "revision_id, triage_run_id, provider, status, approved_by_membership_id, "
                    "idempotency_key, body_sha256, claimed_at) VALUES (CAST(:org AS uuid), "
                    "CAST(:ticket AS uuid), CAST(:mailbox AS uuid), CAST(:revision AS uuid), "
                    "CAST(:run AS uuid), 'gmail', 'PENDING', CAST(:membership AS uuid), :key, "
                    ":digest, :at) RETURNING id::text"
                ),
                {
                    "org": request.organization_id,
                    "ticket": facts.ticket_id,
                    "mailbox": facts.mailbox_id,
                    "revision": revision_id,
                    "run": facts.triage_run_id,
                    "membership": request.actor.membership_id,
                    "key": f"{request.idempotency_key}:draft",
                    "digest": digest(body),
                    "at": at,
                },
            ).scalar_one()
        return ClaimedDraft(
            draft_id=str(draft_id), revision_id=str(revision_id), revision=revision or 1, body=body
        )

    def settle_provider_draft(
        self, organization_id: str, draft_id: str, outcome: DraftOutcome, *, at: datetime
    ) -> None:
        with self._tx() as c:
            c.execute(
                text(
                    "UPDATE provider_drafts SET status = :status, failure_code = :code, "
                    "provider_draft_id = :provider_id, provider_thread_id = :thread, "
                    "settled_at = :at WHERE id = CAST(:id AS uuid) "
                    "AND organization_id = CAST(:org AS uuid) AND status = 'PENDING'"
                ),
                {
                    "id": draft_id,
                    "org": organization_id,
                    "status": outcome.status,
                    "code": outcome.failure_code,
                    "provider_id": outcome.provider_draft_id,
                    "thread": outcome.provider_thread_id,
                    "at": at,
                },
            )

    def record_draft_action(
        self, request: ReviewRequest, facts: TicketFacts, outcome: DraftOutcome, *, at: datetime
    ) -> None:
        """The provider draft is its own attributable action."""
        with self._tx() as c:
            c.execute(
                text(
                    "INSERT INTO actions (organization_id, ticket_id, triage_run_id, "
                    "actor_membership_id, action_type, idempotency_key, reason, outcome, "
                    "error_code, payload, occurred_at) VALUES (CAST(:org AS uuid), "
                    "CAST(:ticket AS uuid), CAST(:run AS uuid), CAST(:membership AS uuid), "
                    "'PROVIDER_DRAFT_CREATED', :key, :reason, :outcome, :code, "
                    "CAST(:payload AS jsonb), :at) ON CONFLICT DO NOTHING"
                ),
                {
                    "org": request.organization_id,
                    "ticket": facts.ticket_id,
                    "run": facts.triage_run_id,
                    "membership": request.actor.membership_id,
                    "key": f"{request.idempotency_key}:draft-action",
                    "reason": request.reason,
                    "outcome": "SUCCEEDED" if outcome.status == "CREATED" else "FAILED",
                    "code": outcome.failure_code,
                    "payload": json.dumps(
                        {
                            "provider": "gmail",
                            "provider_draft_id": outcome.provider_draft_id,
                            "status": outcome.status,
                        },
                        sort_keys=True,
                    ),
                    "at": at,
                },
            )

    def attach_draft_outcome(
        self, organization_id: str, action_id: str, outcome: DraftOutcome
    ) -> None:
        """So a replay of the approval reports the same provider result."""
        with self._tx() as c:
            c.execute(
                text(
                    "UPDATE actions SET payload = payload || CAST(:patch AS jsonb) "
                    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid)"
                ),
                {
                    "id": action_id,
                    "org": organization_id,
                    "patch": json.dumps(
                        {
                            "draft": {
                                "status": outcome.status,
                                "failure_code": outcome.failure_code,
                                "provider_draft_id": outcome.provider_draft_id,
                                "provider_thread_id": outcome.provider_thread_id,
                            }
                        }
                    ),
                },
            )

    def release_stale_claims(self, before: datetime, *, at: datetime) -> int:
        """A claim whose worker died is closed as unknown, never silently reused."""
        with self._tx() as c:
            released = c.execute(
                text(
                    "UPDATE provider_drafts SET status = 'FAILED', "
                    "failure_code = 'DRAFT_OUTCOME_UNKNOWN', settled_at = :at "
                    "WHERE status = 'PENDING' AND claimed_at < :before RETURNING id"
                ),
                {"before": before, "at": at},
            ).all()
        return len(released)

    # ------------------------------------------------------------------
    # Statement helpers
    # ------------------------------------------------------------------

    def _insert_revision(
        self,
        c: Any,
        request: ReviewRequest,
        facts: TicketFacts,
        *,
        body: str,
        source: str,
        revision: int,
        at: datetime,
    ) -> str:
        return str(
            c.execute(
                text(
                    "INSERT INTO draft_revisions (organization_id, ticket_id, triage_run_id, "
                    "revision, source, body, body_sha256, citations, created_by_membership_id, "
                    "created_at) VALUES (CAST(:org AS uuid), CAST(:ticket AS uuid), "
                    "CAST(:run AS uuid), :revision, :source, :body, :digest, :citations, "
                    "CAST(:membership AS uuid), :at) RETURNING id::text"
                ),
                {
                    "org": request.organization_id,
                    "ticket": facts.ticket_id,
                    "run": facts.triage_run_id,
                    "revision": revision,
                    "source": source,
                    "body": body,
                    "digest": digest(body),
                    "citations": list(facts.citations),
                    "membership": request.actor.membership_id if source == "HUMAN" else None,
                    "at": at,
                },
            ).scalar_one()
        )

    def _update_ticket(
        self, c: Any, request: ReviewRequest, facts: TicketFacts, *, at: datetime
    ) -> int:
        decision = request.decision
        updates = ["version = version + 1", "updated_at = now()"]
        params: dict[str, Any] = {
            "ticket": facts.ticket_id,
            "org": request.organization_id,
            "expected": request.expected_version,
        }

        status = STATUS_AFTER[decision]
        if status is not None:
            updates.append("status = CAST(:status AS ticket_status)")
            params["status"] = status
        if decision is Decision.RESOLVE:
            updates.append("resolved_at = COALESCE(resolved_at, :at)")
            params["at"] = at
        if decision is Decision.ASSIGN:
            updates.append("assigned_membership_id = CAST(:assignee AS uuid)")
            params["assignee"] = request.assignee_membership_id
        if decision is Decision.REROUTE:
            if request.queue_id:
                updates.append("queue_id = CAST(:queue AS uuid)")
                params["queue"] = request.queue_id
            if request.department_id:
                updates.append("department_id = CAST(:department AS uuid)")
                params["department"] = request.department_id

        row = c.execute(
            text(
                f"UPDATE tickets SET {', '.join(updates)} WHERE id = CAST(:ticket AS uuid) "
                "AND organization_id = CAST(:org AS uuid) AND version = :expected "
                "RETURNING version"
            ),
            params,
        ).first()
        if row is None:
            current = c.execute(
                text(
                    "SELECT version FROM tickets WHERE id = CAST(:ticket AS uuid) "
                    "AND organization_id = CAST(:org AS uuid)"
                ),
                {"ticket": facts.ticket_id, "org": request.organization_id},
            ).scalar()
            raise VersionConflict(int(current) if current is not None else facts.version)
        return int(row.version)

    def _insert_action(
        self,
        c: Any,
        request: ReviewRequest,
        *,
        outcome: str,
        error_code: str | None,
        version_before: int | None,
        version_after: int | None,
        payload: dict[str, Any],
        at: datetime,
        idempotency_key: str | None,
    ) -> str:
        return str(
            c.execute(
                text(
                    "INSERT INTO actions (organization_id, ticket_id, triage_run_id, "
                    "actor_membership_id, action_type, idempotency_key, reason, outcome, "
                    "error_code, ticket_version_before, ticket_version_after, payload, "
                    "occurred_at) VALUES (CAST(:org AS uuid), CAST(:ticket AS uuid), "
                    "CAST(:run AS uuid), CAST(:membership AS uuid), "
                    "CAST(:action_type AS action_type), :key, :reason, :outcome, :code, "
                    ":before, :after, CAST(:payload AS jsonb), :at) RETURNING id::text"
                ),
                {
                    "org": request.organization_id,
                    "ticket": request.ticket_id,
                    "run": payload.get("triage_run_id"),
                    "membership": request.actor.membership_id,
                    "action_type": ACTION_TYPES[request.decision],
                    "key": idempotency_key,
                    "reason": request.reason,
                    "outcome": outcome,
                    "code": error_code,
                    "before": version_before,
                    "after": version_after,
                    "payload": json.dumps(payload, sort_keys=True, default=str),
                    "at": at,
                },
            ).scalar_one()
        )

    def _audit(
        self,
        c: Any,
        request: ReviewRequest,
        *,
        action: str,
        outcome: str,
        reason_code: str | None,
    ) -> None:
        c.execute(
            text(
                "INSERT INTO audit_events (organization_id, actor_membership_id, action, outcome, "
                "reason_code, target_type, target_id, metadata) VALUES (CAST(:org AS uuid), "
                "CAST(:membership AS uuid), :action, :outcome, :reason, 'ticket', :target, "
                "CAST(:metadata AS jsonb))"
            ),
            {
                "org": request.organization_id,
                "membership": request.actor.membership_id,
                "action": action,
                "outcome": outcome,
                "reason": reason_code,
                "target": request.ticket_id,
                "metadata": json.dumps(
                    {
                        "decision": request.decision.value,
                        "expected_version": request.expected_version,
                    },
                    sort_keys=True,
                ),
            },
        )
