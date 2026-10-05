"""Parsing and vetting Gmail push notifications delivered through Pub/Sub.

The request itself is authenticated before it reaches this code: C02 configures
the push subscription with an OIDC token for a dedicated invoker identity, and
Cloud Run rejects anything else at the edge. What is left to do here is decide
whether the *payload* is usable, and it is treated as untrusted:

* A Gmail notification says only "something changed in this mailbox". It
  carries no message content, and its `historyId` is a hint. Sync always reads
  from the mailbox's own stored cursor, so a replayed or reordered
  notification cannot rewind anything.
* The Pub/Sub `messageId` is the deduplication key. Pub/Sub guarantees
  at-least-once delivery, so the same notification will arrive twice.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Pub/Sub message ids are numeric strings today, but the documented contract is
# just "opaque string", so this only bounds length and character set.
MESSAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]{1,255}$")
HISTORY_ID_PATTERN = re.compile(r"^[0-9]{1,20}$")
MAX_ENVELOPE_BYTES = 64 * 1024


class NotificationRefused(Exception):
    """The envelope cannot be turned into a usable notification."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Notification:
    message_id: str
    email_address: str
    history_id: str
    published_at: datetime | None

    @property
    def history_id_int(self) -> int:
        return int(self.history_id)


def _decode_data(raw: str) -> dict[str, Any]:
    try:
        # Pub/Sub uses standard base64; padding is sometimes stripped by proxies.
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded, validate=False)
    except (binascii.Error, ValueError) as error:
        raise NotificationRefused("DATA_NOT_BASE64") from error

    if len(decoded) > MAX_ENVELOPE_BYTES:
        raise NotificationRefused("DATA_TOO_LARGE")

    try:
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NotificationRefused("DATA_NOT_JSON") from error

    if not isinstance(payload, dict):
        raise NotificationRefused("DATA_NOT_AN_OBJECT")
    return payload


def _published_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_push_envelope(envelope: Any) -> Notification:
    """Turn a Pub/Sub push body into a `Notification`, or refuse it."""
    if not isinstance(envelope, dict):
        raise NotificationRefused("ENVELOPE_NOT_AN_OBJECT")

    message = envelope.get("message")
    if not isinstance(message, dict):
        raise NotificationRefused("MESSAGE_MISSING")

    message_id = str(message.get("messageId") or message.get("message_id") or "")
    if not MESSAGE_ID_PATTERN.fullmatch(message_id):
        # Without a usable id there is no deduplication key, and accepting the
        # notification would risk processing it repeatedly.
        raise NotificationRefused("MESSAGE_ID_MISSING")

    data = message.get("data")
    if not isinstance(data, str) or not data:
        raise NotificationRefused("DATA_MISSING")

    payload = _decode_data(data)

    address = str(payload.get("emailAddress") or "").strip().lower()
    if not address or address.count("@") != 1 or any(c.isspace() for c in address):
        raise NotificationRefused("EMAIL_ADDRESS_INVALID")

    history_id = str(payload.get("historyId") or "").strip()
    if not HISTORY_ID_PATTERN.fullmatch(history_id):
        raise NotificationRefused("HISTORY_ID_INVALID")

    return Notification(
        message_id=message_id,
        email_address=address,
        history_id=history_id,
        published_at=_published_at(message.get("publishTime") or message.get("publish_time")),
    )


def notification_job_key(notification: Notification) -> str:
    """Deduplication key for the notification.

    The Pub/Sub message id alone. Including the history id would let a
    redelivery of the same notification through whenever Gmail restated it.
    """
    return notification.message_id
