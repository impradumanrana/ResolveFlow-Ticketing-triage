"""Creating a draft in the client's mailbox. Never sending one.

This module has exactly one provider call: `POST /gmail/v1/users/me/drafts`,
which creates a draft a person then reviews and sends themselves in Gmail. The
send endpoints - `drafts/send` and `messages/send` - appear nowhere in this
codebase, and a test asserts that for every file.

Whether the call can happen is a property of the mailbox, not of this module.
A real composer requires three things at once, and any one missing means the
approval is still recorded while the conversation stays with a person:

* the deployment running the `read_and_draft` scope profile, so consent asked
  for `gmail.compose` at all;
* that mailbox's grant actually carrying it - a person who unticks the box on
  Google's consent screen connects read-only, and this refuses with
  `DRAFT_SCOPE_NOT_GRANTED`;
* a transport and a token provider wired in, which `app.api.review` supplies
  only under that profile.

So the read-only deployment behaves exactly as it did before the scope change:
`composer_for` returns `RefusingComposer`.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol

from app.mailbox.scopes import GMAIL_COMPOSE, GMAIL_READONLY, drafting_available

# Re-exported: callers of this module ask it what a draft needs, and the scope
# policy stays the one definition.
__all__ = [
    "DRAFTS_ENDPOINT",
    "GMAIL_COMPOSE",
    "CreatedDraft",
    "DraftRefused",
    "GmailDraftComposer",
    "RefusingComposer",
    "can_create_drafts",
    "composer_for",
]

DRAFTS_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
DEFAULT_TIMEOUT_SECONDS = 20.0


class DraftRefused(Exception):
    """No draft was created. `transient` says whether retrying could help."""

    def __init__(self, code: str, *, transient: bool = False, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.status = status


@dataclass(frozen=True)
class CreatedDraft:
    provider_draft_id: str
    provider_thread_id: str | None


class Composer(Protocol):
    def create(self, access_token: str, *, raw: str, thread_id: str | None) -> CreatedDraft: ...


class RefusingComposer:
    """The composer a read-only mailbox gets."""

    def __init__(self, code: str = "DRAFT_SCOPE_NOT_GRANTED"):
        self.code = code

    def create(self, access_token: str, *, raw: str, thread_id: str | None) -> CreatedDraft:
        raise DraftRefused(self.code)


class GmailDraftComposer:
    """Creates a Gmail draft over the C06 transport. Has no send path."""

    def __init__(self, transport: Any, *, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.transport = transport
        self.timeout = timeout

    def create(self, access_token: str, *, raw: str, thread_id: str | None) -> CreatedDraft:
        message: dict[str, Any] = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id

        response = self.transport.request(
            "POST",
            DRAFTS_ENDPOINT,
            json={"message": message},
            bearer=access_token,
            timeout=self.timeout,
        )
        status = getattr(response, "status", 0)
        body = getattr(response, "body", {}) or {}

        if status in (200, 201):
            draft_id = body.get("id")
            if not isinstance(draft_id, str) or not draft_id:
                raise DraftRefused("DRAFT_RESPONSE_INVALID", status=status)
            created_message = body.get("message") or {}
            thread = created_message.get("threadId")
            return CreatedDraft(
                provider_draft_id=draft_id,
                provider_thread_id=thread if isinstance(thread, str) else None,
            )

        raise DraftRefused(
            _code_for(status), transient=status in (429, 500, 502, 503, 504), status=status
        )


def _code_for(status: int) -> str:
    if status in (401, 403):
        # The mailbox cannot write, or the grant was withdrawn.
        return "DRAFT_NOT_PERMITTED"
    if status == 404:
        return "THREAD_NOT_FOUND"
    if status == 429:
        return "PROVIDER_RATE_LIMITED"
    if status >= 500:
        return "PROVIDER_UNAVAILABLE"
    return "DRAFT_REQUEST_REJECTED"


def can_create_drafts(granted_scopes: Collection[str]) -> bool:
    # One definition of "can hold a draft", shared with the scope policy that
    # decides what consent asks for.
    return drafting_available(granted_scopes)


def composer_for(granted_scopes: Collection[str], transport: Any | None) -> Composer:
    """A real composer only for a mailbox that was actually granted the scope."""
    scopes = set(granted_scopes)
    if not can_create_drafts(scopes) or transport is None:
        return RefusingComposer()
    if GMAIL_READONLY not in scopes:  # pragma: no cover - defensive
        return RefusingComposer("MAILBOX_SCOPE_DENIED")
    return GmailDraftComposer(transport)
