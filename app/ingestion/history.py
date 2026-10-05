"""Folding Gmail history records into what ingestion must actually do.

Gmail's history is a log of changes since a cursor, delivered in pages, with no
guarantee that a given message appears once. A message can be added and deleted
within the same page, or appear in several records.

Two rules make this safe:

* **The cursor never moves backwards.** A delayed or out-of-order notification
  would otherwise rewind the mailbox and reprocess everything after it.
* **Deletion wins over addition.** If a message was added and then deleted in
  the same window, fetching it would fail; treating it as deleted is both
  correct and avoids a pointless call.

Pure: no client, no clock, no storage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HistoryEffects:
    added_message_ids: tuple[str, ...]
    deleted_message_ids: tuple[str, ...]
    highest_history_id: str | None

    @property
    def is_empty(self) -> bool:
        return not self.added_message_ids and not self.deleted_message_ids


def _message_ids(record: dict[str, Any], key: str) -> list[str]:
    entries = record.get(key) or []
    if not isinstance(entries, list):
        return []
    ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        message = entry.get("message")
        if isinstance(message, dict) and message.get("id"):
            ids.append(str(message["id"]))
    return ids


def compute_effects(records: Iterable[dict[str, Any]]) -> HistoryEffects:
    """Fold history records into ordered, de-duplicated effects."""
    added: dict[str, None] = {}
    deleted: dict[str, None] = {}
    highest: int | None = None

    for record in records:
        if not isinstance(record, dict):
            continue

        raw_id = record.get("id")
        if raw_id is not None and str(raw_id).isdigit():
            value = int(raw_id)
            highest = value if highest is None else max(highest, value)

        for message_id in _message_ids(record, "messagesAdded"):
            added[message_id] = None
        for message_id in _message_ids(record, "messagesDeleted"):
            deleted[message_id] = None

    # A message deleted within the window is not fetched, whatever order the
    # records arrived in.
    for message_id in deleted:
        added.pop(message_id, None)

    return HistoryEffects(
        added_message_ids=tuple(added),
        deleted_message_ids=tuple(deleted),
        highest_history_id=str(highest) if highest is not None else None,
    )


def advance_cursor(current: str | None, observed: str | None) -> str | None:
    """Monotonic cursor. Returns the larger of the two, never a smaller value."""
    if observed is None or not str(observed).isdigit():
        return current
    if current is None or not str(current).isdigit():
        return str(observed)
    return str(max(int(current), int(observed)))


def is_behind(current: str | None, candidate: str | None) -> bool:
    """True when `candidate` is newer than the stored cursor."""
    if candidate is None or not str(candidate).isdigit():
        return False
    if current is None or not str(current).isdigit():
        return True
    return int(candidate) > int(current)
