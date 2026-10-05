"""What the provider draft would contain, built before anything is created.

A reviewer approves a specific reply to a specific person. So the preview is
the thing that gets created - same recipient, same subject, same body, same
thread - and the MIME it produces is built from the preview, not alongside it.

Two habits from earlier phases apply here, in the other direction. C07 treated
inbound headers as untrusted; outbound headers are built from stored values and
sanitised the same way, because a newline in a subject is a header injection.
And nothing is added to the body: no signature, no footer, no "sent by AI"
disclaimer the client did not ask for.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

MAX_SUBJECT_CHARACTERS = 300
MAX_HEADER_RECIPIENTS = 10
CONTROL_CHARACTERS = re.compile(r"[\r\n\t\x00-\x1f\x7f]")
ADDRESS = re.compile(
    r"^[^@\s,;<>\"]+@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)


class PreviewRefused(Exception):
    """A draft cannot be addressed or built."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def clean_header(value: str | None) -> str:
    """One line, no control characters, trimmed."""
    return CONTROL_CHARACTERS.sub(" ", str(value or "")).strip()


def is_address(value: str | None) -> bool:
    address = parseaddr(str(value or ""))[1]
    return bool(address) and bool(ADDRESS.fullmatch(address))


def reply_subject(subject: str | None) -> str:
    """ "Re:" once, however many the original carried."""
    cleaned = clean_header(subject) or "(no subject)"
    while cleaned[:3].lower() == "re:":
        cleaned = cleaned[3:].strip()
    return f"Re: {cleaned}"[:MAX_SUBJECT_CHARACTERS]


@dataclass(frozen=True)
class InboundMessage:
    """The message being replied to."""

    from_address: str
    subject: str | None = None
    rfc822_message_id: str | None = None
    references: tuple[str, ...] = ()
    to_addresses: tuple[str, ...] = ()
    cc_addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftPreview:
    mailbox_address: str
    to: tuple[str, ...]
    subject: str
    body: str
    thread_id: str
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_mime(self) -> bytes:
        message = EmailMessage()
        message["From"] = formataddr(("", self.mailbox_address))
        message["To"] = ", ".join(self.to)
        if self.cc:
            message["Cc"] = ", ".join(self.cc)
        message["Subject"] = self.subject
        if self.in_reply_to:
            message["In-Reply-To"] = self.in_reply_to
        if self.references:
            message["References"] = " ".join(self.references)
        message.set_content(self.body)
        return message.as_bytes()

    def raw(self) -> str:
        """Base64url, as the Gmail API takes it."""
        return base64.urlsafe_b64encode(self.to_mime()).decode("ascii")


def build_preview(
    *,
    mailbox_address: str,
    thread_id: str,
    body: str,
    inbound: InboundMessage | None,
    customer_address: str | None = None,
) -> DraftPreview:
    """The draft that would be created. Refuses rather than guessing a recipient."""
    if not clean_header(body) or not body.strip():
        raise PreviewRefused("DRAFT_BODY_EMPTY")
    if not is_address(mailbox_address):
        raise PreviewRefused("MAILBOX_ADDRESS_INVALID")

    recipient = None
    for candidate in (inbound.from_address if inbound else None, customer_address):
        if is_address(candidate):
            recipient = parseaddr(str(candidate))[1].lower()
            break
    if recipient is None:
        # Better no draft than a draft addressed to a guess.
        raise PreviewRefused("NO_CUSTOMER_ADDRESS")

    notes: list[str] = []
    cc: tuple[str, ...] = ()
    if inbound and inbound.cc_addresses:
        # Other people were on the original. Whether to include them is the
        # reviewer's decision, so the preview says so rather than deciding.
        notes.append(
            f"The original message copied {len(inbound.cc_addresses)} other address(es); "
            "the draft replies only to the sender."
        )

    references = tuple(
        clean_header(reference)
        for reference in (
            *(inbound.references if inbound else ()),
            *([inbound.rfc822_message_id] if inbound and inbound.rfc822_message_id else []),
        )
        if clean_header(reference)
    )[-MAX_HEADER_RECIPIENTS:]

    return DraftPreview(
        mailbox_address=parseaddr(mailbox_address)[1].lower(),
        to=(recipient,),
        subject=reply_subject(inbound.subject if inbound else None),
        body=body.strip(),
        thread_id=thread_id,
        in_reply_to=clean_header(inbound.rfc822_message_id)
        if inbound and inbound.rfc822_message_id
        else None,
        references=references,
        cc=cc,
        notes=tuple(notes),
    )
