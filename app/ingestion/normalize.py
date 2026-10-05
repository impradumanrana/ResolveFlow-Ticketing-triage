"""Turning a Gmail message resource into the rows C04 defined.

Email is untrusted input, and this is where it first becomes product data.
Everything here is defensive:

* **Timestamps come from `internalDate`, not the `Date` header.** The header is
  written by the sender and is trivially wrong or forged; `internalDate` is
  Gmail's own receipt time and is what SLA and ordering must use.
* **Header-derived values are stripped of CR and LF.** A header carrying a
  newline is how injection attacks travel into anything that later emits
  headers or logs.
* **Filenames are reduced to a basename** with path separators and control
  characters removed, so a crafted attachment name cannot traverse a path.
* **Bodies and headers are bounded.** A megabyte of subject line is an attack,
  not a support request.
* **Attachments are classified, never fetched here.** Type and size policy
  decide whether a file is a candidate for download and scanning at all.

Pure: no network, no clock, no storage.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from typing import Any

MAX_SUBJECT_CHARS = 998  # RFC 5322 line limit; anything longer is not a subject.
MAX_BODY_CHARS = 256 * 1024
MAX_SNIPPET_CHARS = 512
MAX_RECIPIENTS = 100
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

TRUNCATION_MARKER = "\n\n[truncated by ResolveFlow]"

# Attachments outside this set are recorded but never downloaded or scanned.
ALLOWED_ATTACHMENT_TYPES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/csv",
        "text/html",
        "image/png",
        "image/jpeg",
        "image/gif",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RUN = re.compile(r"[ \t]+")


class _HtmlText(HTMLParser):
    """Minimal HTML-to-text for email bodies.

    Deliberately local rather than shared with knowledge ingestion: that parser
    is tuned for documents being indexed, this one for a reply body where line
    structure carries meaning. Script and style content is dropped.
    """

    _SKIP = {"script", "style", "head", "noscript"}
    _BREAK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


@dataclass(frozen=True)
class AttachmentCandidate:
    provider_attachment_id: str | None
    filename: str
    content_type: str
    size_bytes: int
    # PENDING means "eligible for download and scanning"; SKIPPED means the
    # policy refused it and nothing will be fetched.
    scan_state: str
    skip_reason: str | None = None


@dataclass(frozen=True)
class NormalizedMessage:
    provider_message_id: str
    provider_thread_id: str
    rfc822_message_id: str | None
    direction: str
    from_address: str | None
    to_addresses: tuple[str, ...]
    cc_addresses: tuple[str, ...]
    subject: str | None
    body_text: str | None
    snippet: str | None
    sent_at: datetime
    has_attachments: bool
    attachments: tuple[AttachmentCandidate, ...] = field(default=())
    label_ids: tuple[str, ...] = field(default=())


class NormalizationError(ValueError):
    """The Gmail resource cannot be turned into a message row."""


def clean_header(value: str | None, *, limit: int = MAX_SUBJECT_CHARS) -> str | None:
    """Strip control characters and collapse whitespace in a header value."""
    if value is None:
        return None
    flattened = value.replace("\r", " ").replace("\n", " ")
    flattened = _CONTROL_CHARACTERS.sub("", flattened)
    flattened = _WHITESPACE_RUN.sub(" ", flattened).strip()
    if not flattened:
        return None
    return flattened[:limit]


def clean_filename(value: str | None) -> str:
    """Reduce an attachment name to a safe basename."""
    if not value:
        return "attachment"
    name = value.replace("\\", "/").split("/")[-1]
    name = _CONTROL_CHARACTERS.sub("", name).replace("\r", "").replace("\n", "").strip()
    name = name.lstrip(".") or "attachment"
    return name[:255]


def normalize_address(value: str | None) -> str | None:
    if not value:
        return None
    address = parseaddr(value)[1].strip().lower()
    if not address or address.count("@") != 1 or any(c.isspace() for c in address):
        return None
    return address[:320]


def normalize_addresses(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    found: dict[str, None] = {}
    for _, raw in getaddresses([value.replace("\r", " ").replace("\n", " ")]):
        address = normalize_address(raw)
        if address:
            found[address] = None
        if len(found) >= MAX_RECIPIENTS:
            break
    return tuple(found)


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for header in payload.get("headers") or []:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name") or "").strip().lower()
        if name and name not in headers:
            headers[name] = str(header.get("value") or "")
    return headers


def _decode_body(part: dict[str, Any]) -> str:
    body = part.get("body") or {}
    data = body.get("data")
    if not isinstance(data, str) or not data:
        return ""
    try:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""


def _walk(part: dict[str, Any]) -> list[dict[str, Any]]:
    parts = [part]
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            parts.extend(_walk(child))
    return parts


def _body_text(payload: dict[str, Any]) -> str | None:
    parts = _walk(payload)

    plain = [p for p in parts if str(p.get("mimeType") or "").startswith("text/plain")]
    for part in plain:
        text = _decode_body(part)
        if text.strip():
            return _bound_body(text)

    html = [p for p in parts if str(p.get("mimeType") or "").startswith("text/html")]
    for part in html:
        raw = _decode_body(part)
        if raw.strip():
            parser = _HtmlText()
            parser.feed(raw)
            parser.close()
            text = re.sub(r"\n{3,}", "\n\n", parser.text())
            if text.strip():
                return _bound_body(text)

    return None


def _bound_body(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= MAX_BODY_CHARS:
        return text
    return text[: MAX_BODY_CHARS - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _attachments(payload: dict[str, Any]) -> tuple[AttachmentCandidate, ...]:
    candidates: list[AttachmentCandidate] = []
    for part in _walk(payload):
        filename = part.get("filename")
        if not filename:
            continue
        body = part.get("body") or {}
        size = int(body.get("size") or 0)
        content_type = (
            str(part.get("mimeType") or "application/octet-stream").split(";")[0].strip().lower()
        )

        skip_reason: str | None = None
        if content_type not in ALLOWED_ATTACHMENT_TYPES:
            skip_reason = "TYPE_NOT_ALLOWED"
        elif size > MAX_ATTACHMENT_BYTES:
            skip_reason = "TOO_LARGE"
        elif size <= 0:
            skip_reason = "EMPTY"

        candidates.append(
            AttachmentCandidate(
                provider_attachment_id=str(body.get("attachmentId"))
                if body.get("attachmentId")
                else None,
                filename=clean_filename(str(filename)),
                content_type=content_type,
                size_bytes=max(0, size),
                scan_state="SKIPPED" if skip_reason else "PENDING",
                skip_reason=skip_reason,
            )
        )
    return tuple(candidates)


def normalize_message(resource: dict[str, Any], *, mailbox_address: str) -> NormalizedMessage:
    """Normalize one Gmail message resource for one mailbox."""
    if not isinstance(resource, dict):
        raise NormalizationError("Message resource is not an object.")

    message_id = str(resource.get("id") or "")
    thread_id = str(resource.get("threadId") or "")
    if not message_id or not thread_id:
        raise NormalizationError("Message resource has no id or threadId.")

    internal_date = resource.get("internalDate")
    if internal_date is None or not str(internal_date).isdigit():
        # Without Gmail's own receipt time there is no trustworthy ordering.
        raise NormalizationError("Message resource has no usable internalDate.")
    sent_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)

    payload = resource.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    headers = _headers(payload)

    from_address = normalize_address(headers.get("from"))
    mailbox = mailbox_address.strip().lower()
    attachments = _attachments(payload)

    return NormalizedMessage(
        provider_message_id=message_id,
        provider_thread_id=thread_id,
        rfc822_message_id=clean_header(headers.get("message-id"), limit=998),
        # A message the mailbox itself sent is outbound; everything else is
        # inbound. Refined when send identities arrive.
        direction="OUTBOUND" if from_address == mailbox else "INBOUND",
        from_address=from_address,
        to_addresses=normalize_addresses(headers.get("to")),
        cc_addresses=normalize_addresses(headers.get("cc")),
        subject=clean_header(headers.get("subject")),
        body_text=_body_text(payload),
        snippet=clean_header(str(resource.get("snippet") or ""), limit=MAX_SNIPPET_CHARS),
        sent_at=sent_at,
        has_attachments=bool(attachments),
        attachments=attachments,
        label_ids=tuple(str(label) for label in (resource.get("labelIds") or []) if label),
    )
