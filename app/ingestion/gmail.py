"""Gmail API calls needed for ingestion, over the shared injectable transport.

Only four operations, all read-only apart from registering and stopping a
watch: watch, history.list, messages.get, and messages.list for backfill. There
is no send, draft, label, or modify call anywhere in this module, and the
connected token could not authorize one (C-D055).

Failures are classified rather than retried blindly, because the right recovery
differs completely: a stale history identifier means "resynchronise from
scratch", a rate limit means "come back later", a revoked grant means "stop and
tell someone", and a deleted message means "skip it".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from app.mailbox.google import DEFAULT_TIMEOUT_SECONDS, Transport

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
WATCH_ENDPOINT = f"{GMAIL_BASE}/watch"
STOP_ENDPOINT = f"{GMAIL_BASE}/stop"
HISTORY_ENDPOINT = f"{GMAIL_BASE}/history"
MESSAGES_ENDPOINT = f"{GMAIL_BASE}/messages"

# Only these two history types matter in V1. Label changes are Gmail's own
# state, not the product's: per-user seen state is ResolveFlow's (C-D042).
HISTORY_TYPES = ("messageAdded", "messageDeleted")

MAX_HISTORY_PAGE = 500
MAX_BACKFILL_PAGE = 100


class GmailError(Exception):
    """A Gmail call failed. `transient` says whether retrying could help."""

    def __init__(self, code: str, *, transient: bool, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.status = status


class HistoryTooOld(GmailError):
    """The stored cursor predates Gmail's retained history.

    Gmail keeps roughly a week. A mailbox that was disconnected, degraded, or
    simply quiet for longer cannot be caught up incrementally and must be
    reconciled by listing messages instead.
    """

    def __init__(self, status: int | None = None):
        super().__init__("HISTORY_TOO_OLD", transient=False, status=status)


class MessageGone(GmailError):
    """The message was deleted between the notification and the fetch."""

    def __init__(self, status: int | None = None):
        super().__init__("MESSAGE_GONE", transient=False, status=status)


class RateLimited(GmailError):
    def __init__(self, status: int | None = None, retry_after_seconds: int | None = None):
        super().__init__("RATE_LIMITED", transient=True, status=status)
        self.retry_after_seconds = retry_after_seconds


class Unauthorized(GmailError):
    """The access token was rejected. The grant may have been revoked."""

    def __init__(self, status: int | None = None):
        super().__init__("UNAUTHORIZED", transient=False, status=status)


@dataclass(frozen=True)
class WatchRegistration:
    history_id: str
    expiration_ms: int


@dataclass(frozen=True)
class HistoryPage:
    records: tuple[dict[str, Any], ...]
    next_page_token: str | None
    latest_history_id: str | None


@dataclass(frozen=True)
class MessagePage:
    message_ids: tuple[str, ...]
    next_page_token: str | None


def _classify(response_status: int, body: dict[str, Any]) -> GmailError:
    error = body.get("error") or {}
    reason = ""
    if isinstance(error, dict):
        details = error.get("errors") or []
        if isinstance(details, list) and details and isinstance(details[0], dict):
            reason = str(details[0].get("reason") or "")
        reason = reason or str(error.get("status") or "")

    if response_status == 404:
        return MessageGone(status=response_status)
    if response_status in (401,):
        return Unauthorized(status=response_status)
    if response_status == 403 and reason.lower() in {
        "ratelimitexceeded",
        "userratelimitexceeded",
        "quotaexceeded",
        "resource_exhausted",
    }:
        return RateLimited(status=response_status)
    if response_status == 403:
        # Access removed while connected: an administrator changed delegation
        # or the mailbox was suspended. Not retryable.
        return Unauthorized(status=response_status)
    if response_status == 429:
        return RateLimited(status=response_status)
    if response_status >= 500:
        return GmailError("GMAIL_UNAVAILABLE", transient=True, status=response_status)
    return GmailError("GMAIL_REQUEST_REJECTED", transient=False, status=response_status)


class GmailClient:
    def __init__(self, transport: Transport, *, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._transport = transport
        self._timeout = timeout

    def _call(
        self,
        method: str,
        url: str,
        access_token: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._transport.request(
            method, url, json=json, bearer=access_token, timeout=self._timeout
        )
        if response.status != 200:
            raise _classify(response.status, response.body)
        return response.body

    def watch(self, access_token: str, *, topic_name: str) -> WatchRegistration:
        """Register a push watch. Gmail expires it after about seven days."""
        body = self._call(
            "POST",
            WATCH_ENDPOINT,
            access_token,
            json={"topicName": topic_name, "labelFilterBehavior": "include"},
        )
        history_id = body.get("historyId")
        expiration = body.get("expiration")
        if not history_id or not expiration:
            raise GmailError("WATCH_RESPONSE_MALFORMED", transient=False)
        return WatchRegistration(history_id=str(history_id), expiration_ms=int(expiration))

    def stop(self, access_token: str) -> None:
        self._call("POST", STOP_ENDPOINT, access_token, json={})

    def list_history(
        self, access_token: str, *, start_history_id: str, page_token: str | None = None
    ) -> HistoryPage:
        """Changes since `start_history_id`. Raises `HistoryTooOld` if stale."""
        params: list[tuple[str, str]] = [
            ("startHistoryId", start_history_id),
            ("maxResults", str(MAX_HISTORY_PAGE)),
        ]
        params.extend(("historyTypes", kind) for kind in HISTORY_TYPES)
        if page_token:
            params.append(("pageToken", page_token))

        response = self._transport.request(
            "GET",
            f"{HISTORY_ENDPOINT}?{urlencode(params)}",
            bearer=access_token,
            timeout=self._timeout,
        )
        if response.status == 404:
            # Gmail answers 404 when the cursor is older than its retained
            # history. This is the one 404 that is not a missing message.
            raise HistoryTooOld(status=response.status)
        if response.status != 200:
            raise _classify(response.status, response.body)

        records = response.body.get("history") or []
        if not isinstance(records, list):
            raise GmailError("HISTORY_RESPONSE_MALFORMED", transient=False)
        latest = response.body.get("historyId")
        token = response.body.get("nextPageToken")
        return HistoryPage(
            records=tuple(record for record in records if isinstance(record, dict)),
            next_page_token=str(token) if token else None,
            latest_history_id=str(latest) if latest else None,
        )

    def get_message(self, access_token: str, message_id: str) -> dict[str, Any]:
        return self._call("GET", f"{MESSAGES_ENDPOINT}/{message_id}?format=full", access_token)

    def list_messages(
        self, access_token: str, *, query: str, page_token: str | None = None
    ) -> MessagePage:
        """Message ids matching a query. Used for reconciliation and backfill."""
        params = [("q", query), ("maxResults", str(MAX_BACKFILL_PAGE))]
        if page_token:
            params.append(("pageToken", page_token))

        body = self._call("GET", f"{MESSAGES_ENDPOINT}?{urlencode(params)}", access_token)
        messages = body.get("messages") or []
        ids = tuple(
            str(item["id"]) for item in messages if isinstance(item, dict) and item.get("id")
        )
        token = body.get("nextPageToken")
        return MessagePage(message_ids=ids, next_page_token=str(token) if token else None)
