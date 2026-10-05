"""The scan gate for attachments (C13).

C07 decided what is *accepted*: an allowlist of content types, a size cap, and
`SKIPPED` for anything outside it, recorded but never fetched. The C04 schema
then promised a second gate - "nothing may read one until the scan state says
it is clean" - and deferred the scanner to this phase. This module is that
gate.

The read path does not exist yet: nothing in the product downloads attachment
bytes, and a test asserts that. So this is written as the single place a future
download has to pass through, with the rules stated now rather than invented on
the day:

* **Only `CLEAN` is readable.** Not `PENDING` - "not yet known to be bad" is
  not "known to be good". A verdict that never arrives therefore fails closed.
* **No scanner means `FAILED`, never `CLEAN`.** An unconfigured deployment
  must not quietly treat absence of evidence as evidence of absence.
* **`SKIPPED` is terminal.** C07 refused to fetch it, so there are no bytes to
  scan and nothing may later claim there were.
* **A `CLEAN` verdict needs a digest and an object.** Clean refers to specific
  bytes; without naming them the verdict means nothing, and the C04 constraint
  `clean_attachment_has_an_object` says the same thing in the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

# C07 owns the intake policy. Imported rather than restated: two copies of a
# size cap is one cap and one bug.
from app.ingestion.normalize import ALLOWED_ATTACHMENT_TYPES, MAX_ATTACHMENT_BYTES

__all__ = [
    "ALLOWED_ATTACHMENT_TYPES",
    "MAX_ATTACHMENT_BYTES",
    "READABLE_STATES",
    "AttachmentRefused",
    "ScanState",
    "ScanVerdict",
    "Scanner",
    "may_read",
    "next_state",
    "refusal_for",
    "require_readable",
]


class ScanState(StrEnum):
    """Mirrors the `scan_state_is_known` constraint in migration 0003."""

    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# Deliberately one state. Anything else is a refusal.
READABLE_STATES: frozenset[str] = frozenset({ScanState.CLEAN})

# What a person is told, per state. None of these says "try again": whether a
# retry is worthwhile is an operator's judgement, not a reader's.
REFUSALS: dict[str, str] = {
    ScanState.PENDING: "ATTACHMENT_NOT_SCANNED",
    ScanState.INFECTED: "ATTACHMENT_INFECTED",
    ScanState.FAILED: "ATTACHMENT_SCAN_FAILED",
    ScanState.SKIPPED: "ATTACHMENT_OUTSIDE_POLICY",
}

# A verdict may only move an attachment from PENDING. Re-scanning something
# already judged would let a later "CLEAN" overwrite an earlier "INFECTED".
TERMINAL_STATES: frozenset[str] = frozenset(
    {ScanState.CLEAN, ScanState.INFECTED, ScanState.SKIPPED}
)


class AttachmentRefused(Exception):
    """An attachment was asked for that may not be read."""

    def __init__(self, code: str, *, state: str):
        super().__init__(code)
        self.code = code
        self.state = state


@dataclass(frozen=True)
class ScanVerdict:
    """What a scanner concluded about specific bytes."""

    state: ScanState
    sha256: str | None = None
    storage_object: str | None = None
    detail: str | None = None
    scanned_at: datetime | None = None


class Scanner(Protocol):
    def scan(self, *, storage_object: str, sha256: str) -> ScanVerdict: ...


class RefusingScanner:
    """The scanner an unconfigured deployment gets.

    Returns `FAILED`, so the attachment stays unreadable and an operator can
    see why. Returning `CLEAN` here, or `PENDING` forever, would both turn "no
    scanner" into "everything is fine".
    """

    code = "NO_SCANNER_CONFIGURED"

    def scan(self, *, storage_object: str, sha256: str) -> ScanVerdict:
        return ScanVerdict(
            state=ScanState.FAILED,
            sha256=sha256,
            storage_object=storage_object,
            detail=self.code,
        )


def may_read(state: str, *, deleted_at: datetime | None = None) -> bool:
    """The gate. One state passes, and a deleted attachment never does."""
    if deleted_at is not None:
        return False
    return state in READABLE_STATES


def refusal_for(state: str) -> str:
    """The code for a state that may not be read."""
    return REFUSALS.get(state, "ATTACHMENT_NOT_AVAILABLE")


def require_readable(state: str, *, deleted_at: datetime | None = None) -> None:
    """Raise unless these bytes may be read. The one call a reader must make."""
    if not may_read(state, deleted_at=deleted_at):
        raise AttachmentRefused(
            "ATTACHMENT_DELETED" if deleted_at is not None else refusal_for(state),
            state=state,
        )


def next_state(current: str, verdict: ScanVerdict) -> ScanState:
    """Apply a verdict to an attachment, or refuse to.

    Raises rather than returning the current state, because silently dropping a
    verdict is how an `INFECTED` result goes missing.
    """
    if current in TERMINAL_STATES:
        raise AttachmentRefused("ATTACHMENT_ALREADY_JUDGED", state=current)
    if current != ScanState.PENDING:
        # FAILED is re-scannable; anything else is not a state a verdict applies to.
        if current != ScanState.FAILED:
            raise AttachmentRefused("ATTACHMENT_STATE_UNKNOWN", state=current)
    if verdict.state is ScanState.CLEAN and not (verdict.sha256 and verdict.storage_object):
        # "Clean" is a statement about specific bytes. Without naming them it
        # is not a statement at all.
        raise AttachmentRefused("CLEAN_VERDICT_WITHOUT_BYTES", state=current)
    if verdict.state in {ScanState.PENDING, ScanState.SKIPPED}:
        raise AttachmentRefused("NOT_A_VERDICT", state=current)
    return verdict.state
