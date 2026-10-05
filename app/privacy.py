"""Data-subject export and erasure (C13).

C04 deliberately left ticket deletion out of the retention sweep, with the
reason recorded in `app.retention`: "deleting a ticket orphans its conversation
and its decision history. Ticket deletion is a privacy-request workflow (C13),
not an age-based sweep." This module is that workflow.

Two operations, with opposite rules about redaction:

* **Export** gives a person everything held about them, unredacted, because
  that is the point. It does redact *other* people: a third party copied on
  one of their emails is not the requester's data, and handing over that
  address would answer one subject-access request by creating a breach for
  someone else.
* **Erasure** keeps the rows and destroys the content. Deleting the rows would
  take the conversation's decision history with it - who reviewed what, when,
  and why - which the client needs and which the audit trail is required to
  retain. So every personal field becomes a tombstone, addresses become a
  reserved `.invalid` address, and the row survives with its timestamps,
  versions and references intact.

What erasure deliberately does **not** touch:

* `audit_events`, which is append-only by database trigger (C04). Audit rows
  are retained by design, and the way that is made compatible with erasure is
  that they must never carry customer content in the first place - identifiers,
  codes and staff actors only. A test asserts that for every writer.
* Objects in the attachment bucket, which live in another system. The erasure
  record names what has to be deleted there, and the runbook says how.

Erasure refuses while a legal hold is active. A hold exists precisely to stop
data being destroyed, and honouring an erasure request over one would be the
more serious failure of the two.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.retention import active_holds
from app.security.redaction import redact

# RFC 2606 reserves `.invalid`, so this can never route anywhere.
ERASED_ADDRESS = "erased@erased.invalid"
TOMBSTONE = "[erased]"
TOMBSTONE_JSON = '{"erased": true}'
# `triage_runs.trace` is constrained to a JSON array (C11), so the tombstone
# has to be one. An empty array would be indistinguishable from a run that
# never recorded a trace; this says the trace was erased.
TOMBSTONE_TRACE = '[{"erased": true}]'

# The role that may erase. The same authority as retention (C03): a deletion
# nobody can undo belongs with the person accountable for the workspace.
ERASURE_ROLES = frozenset({"OWNER"})

# Holds that stop an erasure. An organization-wide hold stops everything;
# these two classes are the ones a customer's data lives in.
BLOCKING_HOLD_CLASSES = frozenset({"__ALL__", "MESSAGE", "TICKET"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PrivacyRefused(Exception):
    """An export or erasure that must not proceed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Actor:
    membership_id: str
    organization_id: str
    role: str


@dataclass(frozen=True)
class SubjectRequest:
    organization_id: str
    address: str
    actor: Actor
    reason: str | None = None


@dataclass(frozen=True)
class SubjectFootprint:
    """Everything this organization holds that is about one address."""

    ticket_ids: tuple[str, ...] = ()
    thread_ids: tuple[str, ...] = ()
    message_ids: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.ticket_ids or self.thread_ids or self.message_ids)


@dataclass(frozen=True)
class ErasureOutcome:
    ok: bool
    code: str
    counts: dict[str, int] = field(default_factory=dict)
    storage_objects: tuple[str, ...] = ()
    record_id: str | None = None


# Deliberately permissive: enough to reject something that cannot be an
# address, not an attempt to validate email, which is a famously bad idea.
# A local part, an `@`, and a domain with a dot.
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")


def normalize_address(address: str) -> str:
    """Addresses are compared case-insensitively, as mail is."""
    cleaned = address.strip().lower()
    if len(cleaned) > 320 or not _ADDRESS.fullmatch(cleaned):
        raise PrivacyRefused("SUBJECT_ADDRESS_INVALID")
    return cleaned


def subject_digest(address: str) -> str:
    """How an erasure is recorded without storing what was erased."""
    return hashlib.sha256(normalize_address(address).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Finding the subject
# --------------------------------------------------------------------------


def find_subject(connection: Any, organization_id: str, address: str) -> SubjectFootprint:
    """Tickets, threads and messages that mention this address.

    Every query is scoped by organization. A subject-access request from one
    client must never reach another client's data, and this is a single-tenant
    deployment only by deployment convention - the scoping is what enforces it.
    """
    from sqlalchemy import text

    subject = normalize_address(address)
    parameters = {"org": organization_id, "address": subject}

    message_rows = connection.execute(
        text(
            "SELECT id, thread_id FROM messages "
            "WHERE organization_id = CAST(:org AS uuid) AND ("
            "  lower(from_address) = :address"
            "  OR EXISTS (SELECT 1 FROM unnest(to_addresses) a WHERE lower(a) = :address)"
            "  OR EXISTS (SELECT 1 FROM unnest(cc_addresses) a WHERE lower(a) = :address))"
        ),
        parameters,
    ).all()

    thread_rows = connection.execute(
        text(
            "SELECT id FROM threads "
            "WHERE organization_id = CAST(:org AS uuid) "
            "AND EXISTS (SELECT 1 FROM unnest(participant_addresses) a WHERE lower(a) = :address)"
        ),
        parameters,
    ).all()

    threads = {str(row[0]) for row in thread_rows} | {str(row[1]) for row in message_rows}

    ticket_rows = connection.execute(
        text(
            "SELECT id FROM tickets WHERE organization_id = CAST(:org AS uuid) "
            "AND (lower(customer_address) = :address "
            "     OR thread_id = ANY(CAST(:threads AS uuid[])))"
        ),
        {**parameters, "threads": sorted(threads)},
    ).all()

    return SubjectFootprint(
        ticket_ids=tuple(sorted(str(row[0]) for row in ticket_rows)),
        thread_ids=tuple(sorted(threads)),
        message_ids=tuple(sorted(str(row[0]) for row in message_rows)),
    )


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def _redact_others(values: Any, subject: str) -> list[str]:
    """Keep the subject's own address; redact everyone else's.

    A third party copied on the subject's email is not the subject's personal
    data. Answering one access request by disclosing someone else's address
    would be a breach, so the shape of the conversation is preserved while the
    other addresses are not.
    """
    kept: list[str] = []
    for value in values or ():
        address = str(value)
        kept.append(address if address.lower() == subject else redact(address))
    return kept


def export_subject(connection: Any, organization_id: str, address: str) -> dict[str, Any]:
    """Everything held about one person, in a form they can read.

    Deliberately not redacted for the subject's own data: they are entitled to
    it. `app.security.redaction` is for diagnostics, and is used here only to
    hide other people.
    """
    from sqlalchemy import text

    subject = normalize_address(address)
    footprint = find_subject(connection, organization_id, subject)
    parameters: dict[str, Any] = {
        "org": organization_id,
        "tickets": list(footprint.ticket_ids),
        "messages": list(footprint.message_ids),
    }

    tickets = [
        {
            "reference": row.reference,
            "subject": row.subject,
            "status": str(row.status),
            "route": str(row.route) if row.route else None,
            "category": row.category,
            "urgency": str(row.urgency) if row.urgency else None,
            "created_at": row.created_at.isoformat(),
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        }
        for row in connection.execute(
            text(
                "SELECT reference, subject, status, route, category, urgency, created_at, "
                "resolved_at FROM tickets WHERE organization_id = CAST(:org AS uuid) "
                "AND id = ANY(CAST(:tickets AS uuid[])) ORDER BY created_at"
            ),
            parameters,
        ).all()
    ]

    messages = [
        {
            "direction": str(row.direction),
            "sent_at": row.sent_at.isoformat(),
            "from_address": row.from_address
            if (row.from_address or "").lower() == subject
            else redact(row.from_address or ""),
            "to_addresses": _redact_others(row.to_addresses, subject),
            "cc_addresses": _redact_others(row.cc_addresses, subject),
            "subject": row.subject,
            "body_text": row.body_text,
            "attachments": [],
        }
        for row in connection.execute(
            text(
                "SELECT id, direction, sent_at, from_address, to_addresses, cc_addresses, "
                "subject, body_text FROM messages WHERE organization_id = CAST(:org AS uuid) "
                "AND id = ANY(CAST(:messages AS uuid[])) ORDER BY sent_at"
            ),
            parameters,
        ).all()
    ]

    attachments = [
        {"filename": row.filename, "content_type": row.content_type, "size_bytes": row.size_bytes}
        for row in connection.execute(
            text(
                "SELECT filename, content_type, size_bytes FROM attachments "
                "WHERE organization_id = CAST(:org AS uuid) "
                "AND message_id = ANY(CAST(:messages AS uuid[])) ORDER BY filename"
            ),
            parameters,
        ).all()
    ]

    replies = [
        {"revision": row.revision, "source": row.source, "body": row.body}
        for row in connection.execute(
            text(
                "SELECT revision, source, body FROM draft_revisions "
                "WHERE organization_id = CAST(:org AS uuid) "
                "AND ticket_id = ANY(CAST(:tickets AS uuid[])) ORDER BY ticket_id, revision"
            ),
            parameters,
        ).all()
    ]

    return {
        "schema_version": "v1",
        "subject_address": subject,
        "generated_at": _utc_now().isoformat(),
        "counts": {
            "tickets": len(tickets),
            "messages": len(messages),
            "attachments": len(attachments),
            "replies": len(replies),
        },
        "tickets": tickets,
        "messages": messages,
        "attachments": attachments,
        "replies_prepared": replies,
        "notes": [
            "Other participants' addresses are redacted: they are not your personal data.",
            "Attachment contents are not included; only what was recorded about them.",
            "Records of who at the support team acted on a conversation are retained "
            "for audit and are not part of this export.",
        ],
    }


# --------------------------------------------------------------------------
# Erasure
# --------------------------------------------------------------------------

# Each statement names the table it scrubs and why that column is personal.
# They run in one transaction, in this order, so a failure leaves nothing
# half-erased.
_ERASURE_STATEMENTS: tuple[tuple[str, str], ...] = (
    # The subject's own words, and the headers that identify them.
    (
        "messages",
        "UPDATE messages SET "
        "  from_address = CASE WHEN lower(from_address) = :address "
        "                      THEN :erased_address ELSE from_address END, "
        "  to_addresses = (SELECT coalesce(array_agg("
        "      CASE WHEN lower(a) = :address THEN :erased_address ELSE a END), '{}') "
        "    FROM unnest(to_addresses) a), "
        "  cc_addresses = (SELECT coalesce(array_agg("
        "      CASE WHEN lower(a) = :address THEN :erased_address ELSE a END), '{}') "
        "    FROM unnest(cc_addresses) a), "
        "  subject = :tombstone, body_text = :tombstone, snippet = :tombstone, "
        "  deleted_at = coalesce(deleted_at, :now), updated_at = :now "
        "WHERE organization_id = CAST(:org AS uuid) AND id = ANY(CAST(:messages AS uuid[]))",
    ),
    # Filenames carry names, invoice numbers and case references.
    (
        "attachments",
        "UPDATE attachments SET filename = :tombstone, "
        "  deleted_at = coalesce(deleted_at, :now), updated_at = :now "
        "WHERE organization_id = CAST(:org AS uuid) "
        "AND message_id = ANY(CAST(:messages AS uuid[]))",
    ),
    (
        "threads",
        "UPDATE threads SET "
        "  participant_addresses = (SELECT coalesce(array_agg("
        "      CASE WHEN lower(a) = :address THEN :erased_address ELSE a END), '{}') "
        "    FROM unnest(participant_addresses) a), "
        "  subject = :tombstone, deleted_at = coalesce(deleted_at, :now), updated_at = :now "
        "WHERE organization_id = CAST(:org AS uuid) AND id = ANY(CAST(:threads AS uuid[]))",
    ),
    (
        "tickets",
        "UPDATE tickets SET customer_address = :erased_address, subject = :tombstone, "
        "  deleted_at = coalesce(deleted_at, :now), updated_at = :now "
        "WHERE organization_id = CAST(:org AS uuid) AND id = ANY(CAST(:tickets AS uuid[]))",
    ),
    # The reply prepared for them quotes their situation. The checksum is
    # recomputed rather than left pointing at text that no longer exists.
    (
        "draft_revisions",
        "UPDATE draft_revisions SET body = :tombstone, body_sha256 = :tombstone_sha256 "
        "WHERE organization_id = CAST(:org AS uuid) "
        "AND ticket_id = ANY(CAST(:tickets AS uuid[]))",
    ),
    # The trace holds the ticket text that was sent to the model.
    (
        "triage_runs",
        "UPDATE triage_runs SET draft = NULL, decision_summary = :tombstone, "
        "  trace = CAST(:tombstone_trace AS jsonb), "
        "  grounding_details = CAST(:tombstone_json AS jsonb), "
        "  deleted_at = coalesce(deleted_at, :now) "
        "WHERE organization_id = CAST(:org AS uuid) "
        "AND ticket_id = ANY(CAST(:tickets AS uuid[]))",
    ),
    # A reviewer's reason can quote the customer. The row stays, so who
    # decided what is still answerable; only the free text goes.
    (
        "actions",
        "UPDATE actions SET reason = NULL, payload = CAST(:tombstone_json AS jsonb) "
        "WHERE organization_id = CAST(:org AS uuid) "
        "AND ticket_id = ANY(CAST(:tickets AS uuid[]))",
    ),
    # A VIP entry is the address itself, so there is nothing to keep.
    (
        "vip_contacts",
        "DELETE FROM vip_contacts WHERE organization_id = CAST(:org AS uuid) "
        "AND lower(email_address) = :address",
    ),
)


def _storage_objects(
    connection: Any, organization_id: str, message_ids: list[str]
) -> tuple[str, ...]:
    """Objects an operator must delete from the bucket.

    The bucket is a different system; erasing the row here does not remove the
    bytes there. Naming them is what makes the remaining step auditable.
    """
    from sqlalchemy import text

    if not message_ids:
        return ()
    rows = connection.execute(
        text(
            "SELECT storage_object FROM attachments "
            "WHERE organization_id = CAST(:org AS uuid) "
            "AND message_id = ANY(CAST(:messages AS uuid[])) AND storage_object IS NOT NULL"
        ),
        {"org": organization_id, "messages": message_ids},
    ).all()
    return tuple(sorted(str(row[0]) for row in rows))


def _previous_erasure(connection: Any, organization_id: str, digest: str) -> str | None:
    from sqlalchemy import text

    row = connection.execute(
        text(
            "SELECT id FROM erasure_records WHERE organization_id = CAST(:org AS uuid) "
            "AND subject_digest = :digest ORDER BY completed_at DESC LIMIT 1"
        ),
        {"org": organization_id, "digest": digest},
    ).first()
    return str(row[0]) if row else None


def erase_subject(
    connection: Any, request: SubjectRequest, *, now: datetime | None = None
) -> ErasureOutcome:
    """Destroy one person's content, keep the decisions, record what happened.

    One transaction: the caller's. A partially erased subject is worse than an
    un-erased one, because nobody can tell which it is.
    """
    from sqlalchemy import text

    moment = now or _utc_now()
    subject = normalize_address(request.address)
    digest = subject_digest(subject)

    if request.actor.organization_id != request.organization_id:
        raise PrivacyRefused("RESOURCE_NOT_IN_ORGANIZATION")
    if request.actor.role not in ERASURE_ROLES:
        raise PrivacyRefused("ROLE_LACKS_PERMISSION")

    held = active_holds(connection, request.organization_id)
    if held & BLOCKING_HOLD_CLASSES:
        # A hold exists to stop data being destroyed. Honouring an erasure
        # over one would be the more serious failure.
        raise PrivacyRefused("LEGAL_HOLD_ACTIVE")

    footprint = find_subject(connection, request.organization_id, subject)
    if footprint.empty:
        previous = _previous_erasure(connection, request.organization_id, digest)
        return ErasureOutcome(
            ok=True,
            code="ALREADY_ERASED" if previous else "SUBJECT_NOT_FOUND",
            record_id=previous,
        )

    objects = _storage_objects(connection, request.organization_id, list(footprint.message_ids))

    parameters: dict[str, Any] = {
        "org": request.organization_id,
        "address": subject,
        "erased_address": ERASED_ADDRESS,
        "tombstone": TOMBSTONE,
        "tombstone_sha256": hashlib.sha256(TOMBSTONE.encode("utf-8")).hexdigest(),
        "tombstone_json": TOMBSTONE_JSON,
        "tombstone_trace": TOMBSTONE_TRACE,
        "now": moment,
        "tickets": list(footprint.ticket_ids),
        "threads": list(footprint.thread_ids),
        "messages": list(footprint.message_ids),
    }

    counts: dict[str, int] = {}
    for table, statement in _ERASURE_STATEMENTS:
        counts[table] = int(connection.execute(text(statement), parameters).rowcount or 0)

    record_id = str(
        connection.execute(
            text(
                "INSERT INTO erasure_records (organization_id, subject_digest, "
                "requested_by_membership_id, reason, counts, storage_objects, completed_at) "
                "VALUES (CAST(:org AS uuid), :digest, CAST(:membership AS uuid), :reason, "
                "CAST(:counts AS jsonb), CAST(:objects AS text[]), :now) RETURNING id"
            ),
            {
                "org": request.organization_id,
                "digest": digest,
                "membership": request.actor.membership_id,
                "reason": request.reason,
                "counts": json.dumps(counts, sort_keys=True),
                "objects": list(objects),
                "now": moment,
            },
        ).scalar_one()
    )

    # The audit row carries counts and the digest, never the address: audit is
    # append-only, so anything written here outlives the erasure itself.
    connection.execute(
        text(
            "INSERT INTO audit_events (organization_id, actor_membership_id, action, outcome, "
            "reason_code, target_type, target_id, metadata) VALUES (CAST(:org AS uuid), "
            "CAST(:membership AS uuid), 'privacy.subject_erased', 'ALLOWED', NULL, "
            "'ERASURE_RECORD', :record, CAST(:metadata AS jsonb))"
        ),
        {
            "org": request.organization_id,
            "membership": request.actor.membership_id,
            "record": record_id,
            "metadata": json.dumps(
                {"subject_digest": digest, "counts": counts, "storage_objects": len(objects)},
                sort_keys=True,
            ),
        },
    )

    return ErasureOutcome(
        ok=True, code="ERASED", counts=counts, storage_objects=objects, record_id=record_id
    )
