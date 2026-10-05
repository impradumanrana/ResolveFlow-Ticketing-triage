"""C12: the draft itself - what it would contain, and the one provider call.

The Gmail path cannot run in this deployment (C06 connects read-only and its
schema refuses to store a compose-scoped credential), so it is proven here
against a simulated Gmail: the request it builds, the response it accepts, and
every failure it maps. When the client authorizes the scope change, this is
already verified.
"""

from __future__ import annotations

import base64
from email import message_from_bytes, policy
from typing import Any

import pytest

from app.mailbox.scopes import GMAIL_READONLY, WRITE_CAPABLE_SCOPES
from app.review.drafts import (
    DRAFTS_ENDPOINT,
    GMAIL_COMPOSE,
    DraftRefused,
    GmailDraftComposer,
    RefusingComposer,
    can_create_drafts,
    composer_for,
)
from app.review.preview import (
    DraftPreview,
    InboundMessage,
    PreviewRefused,
    build_preview,
    clean_header,
    is_address,
    reply_subject,
)

BODY = "Open the sign-in page and choose Forgot password. [KB-001]"


class FakeGmail:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None):
        self.status = status
        self.body = body if body is not None else {"id": "draft-7", "message": {"threadId": "t-1"}}
        self.requests: list[dict[str, Any]] = []

    def request(self, method, url, *, json=None, bearer=None, timeout=None, form=None):
        self.requests.append(
            {"method": method, "url": url, "json": json, "bearer": bearer, "timeout": timeout}
        )
        return type("Response", (), {"status": self.status, "body": self.body})()


def preview(**overrides) -> DraftPreview:
    base = {
        "mailbox_address": "support@acme.example",
        "thread_id": "t-1",
        "body": BODY,
        "inbound": InboundMessage(
            from_address="customer@example.net",
            subject="Reset my password",
            rfc822_message_id="<first@mail>",
        ),
    }
    return build_preview(**{**base, **overrides})


# --------------------------------------------------------------------------
# The preview
# --------------------------------------------------------------------------


def test_the_draft_replies_to_the_person_who_wrote_in():
    built = preview()
    assert built.to == ("customer@example.net",)
    assert built.subject == "Re: Reset my password"
    assert built.in_reply_to == "<first@mail>"
    assert built.references == ("<first@mail>",)
    assert built.body == BODY
    assert built.cc == ()


def test_the_mime_is_a_reply_in_the_same_thread():
    message = message_from_bytes(preview().to_mime(), policy=policy.default)
    assert message["To"] == "customer@example.net"
    assert message["From"] == "support@acme.example"
    assert message["Subject"] == "Re: Reset my password"
    assert message["In-Reply-To"] == "<first@mail>"
    assert message["Bcc"] is None
    assert message.get_content().strip() == BODY


def test_the_raw_form_is_base64url_as_the_api_takes_it():
    raw = preview().raw()
    assert "+" not in raw and "/" not in raw
    assert b"Subject: Re: Reset my password" in base64.urlsafe_b64decode(raw)


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Reset my password", "Re: Reset my password"),
        ("Re: Reset my password", "Re: Reset my password"),
        ("RE: re: Reset my password", "Re: Reset my password"),
        (None, "Re: (no subject)"),
        ("", "Re: (no subject)"),
    ],
)
def test_the_subject_says_re_once(subject, expected):
    assert reply_subject(subject) == expected


def test_a_header_cannot_carry_a_newline():
    """A subject with CRLF is a header injection, not a subject (C07's lesson)."""
    built = preview(
        inbound=InboundMessage(
            from_address="customer@example.net",
            subject="Reset\r\nBcc: attacker@example.net",
            rfc822_message_id="<a@b>\r\nX-Evil: 1",
        )
    )
    # The text survives as a subject; what must not survive is a second header.
    assert "\n" not in built.subject and "\r" not in built.subject
    message = message_from_bytes(built.to_mime(), policy=policy.default)
    assert message["Bcc"] is None
    assert len(message.get_all("Subject") or []) == 1
    assert message.get_all("X-Evil") is None
    assert "\nBcc:" not in built.to_mime().decode()


def test_the_body_is_the_approved_text_and_nothing_else():
    built = preview(body="  Exactly this.  ")
    assert built.body == "Exactly this."
    assert (
        message_from_bytes(built.to_mime(), policy=policy.default).get_content().strip()
        == "Exactly this."
    )


def test_other_recipients_are_reported_rather_than_guessed_at():
    built = preview(
        inbound=InboundMessage(
            from_address="customer@example.net",
            subject="Reset",
            cc_addresses=("colleague@example.net", "manager@example.net"),
        )
    )
    assert built.cc == ()
    assert built.notes and "2 other address" in built.notes[0]


def test_the_customer_address_is_used_when_there_is_no_inbound_message():
    built = preview(inbound=None, customer_address="Someone <someone@example.net>")
    assert built.to == ("someone@example.net",)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"body": "   "}, "DRAFT_BODY_EMPTY"),
        ({"inbound": None, "customer_address": None}, "NO_CUSTOMER_ADDRESS"),
        ({"inbound": None, "customer_address": "not-an-address"}, "NO_CUSTOMER_ADDRESS"),
        ({"mailbox_address": "not-an-address"}, "MAILBOX_ADDRESS_INVALID"),
    ],
)
def test_a_draft_that_cannot_be_addressed_is_refused(overrides, code):
    with pytest.raises(PreviewRefused) as refused:
        preview(**overrides)
    assert refused.value.code == code


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("customer@example.net", True),
        ("Customer <customer@example.net>", True),
        ("customer@example", False),
        ("customer@@example.net", False),
        ("customer@example.net, other@example.net", False),
        ("", False),
        (None, False),
    ],
)
def test_addresses_are_checked(value, valid):
    assert is_address(value) is valid


def test_control_characters_never_survive_a_header():
    # Each control character becomes a space; the value stays one line.
    assert clean_header("a\r\nb\tc\x00d") == "a  b c d"
    assert clean_header("  padded  ") == "padded"
    assert clean_header(None) == ""


# --------------------------------------------------------------------------
# The provider call
# --------------------------------------------------------------------------


def test_creating_a_draft_posts_to_the_drafts_endpoint_only():
    gmail = FakeGmail()
    created = GmailDraftComposer(gmail).create("ya29.token", raw="cmF3", thread_id="t-1")

    assert created.provider_draft_id == "draft-7"
    assert created.provider_thread_id == "t-1"
    (call,) = gmail.requests
    assert call["method"] == "POST"
    assert call["url"] == DRAFTS_ENDPOINT
    assert call["url"].endswith("/drafts"), "no send path"
    assert call["json"] == {"message": {"raw": "cmF3", "threadId": "t-1"}}
    assert call["bearer"] == "ya29.token"


def test_a_draft_without_a_thread_is_still_created():
    gmail = FakeGmail()
    GmailDraftComposer(gmail).create("token", raw="cmF3", thread_id=None)
    assert gmail.requests[0]["json"] == {"message": {"raw": "cmF3"}}


@pytest.mark.parametrize(
    ("status", "code", "transient"),
    [
        (401, "DRAFT_NOT_PERMITTED", False),
        (403, "DRAFT_NOT_PERMITTED", False),
        (404, "THREAD_NOT_FOUND", False),
        (400, "DRAFT_REQUEST_REJECTED", False),
        (429, "PROVIDER_RATE_LIMITED", True),
        (500, "PROVIDER_UNAVAILABLE", True),
        (503, "PROVIDER_UNAVAILABLE", True),
    ],
)
def test_every_provider_failure_becomes_a_code(status, code, transient):
    with pytest.raises(DraftRefused) as refused:
        GmailDraftComposer(FakeGmail(status=status, body={})).create(
            "t", raw="cmF3", thread_id=None
        )
    assert refused.value.code == code
    assert refused.value.transient is transient
    assert refused.value.status == status


def test_a_response_without_a_draft_id_is_not_treated_as_success():
    with pytest.raises(DraftRefused) as refused:
        GmailDraftComposer(FakeGmail(body={"message": {"threadId": "t-1"}})).create(
            "t", raw="cmF3", thread_id="t-1"
        )
    assert refused.value.code == "DRAFT_RESPONSE_INVALID"


# --------------------------------------------------------------------------
# The capability is off unless the mailbox was granted it
# --------------------------------------------------------------------------


def test_a_read_only_mailbox_gets_a_composer_that_refuses():
    composer = composer_for((GMAIL_READONLY,), FakeGmail())
    assert isinstance(composer, RefusingComposer)
    with pytest.raises(DraftRefused) as refused:
        composer.create("token", raw="cmF3", thread_id=None)
    assert refused.value.code == "DRAFT_SCOPE_NOT_GRANTED"


def test_only_the_compose_scope_enables_a_real_composer():
    assert can_create_drafts((GMAIL_READONLY, GMAIL_COMPOSE)) is True
    assert can_create_drafts((GMAIL_READONLY,)) is False
    assert isinstance(
        composer_for((GMAIL_READONLY, GMAIL_COMPOSE), FakeGmail()), GmailDraftComposer
    )
    # No transport means no provider call, whatever the scopes say.
    assert isinstance(composer_for((GMAIL_READONLY, GMAIL_COMPOSE), None), RefusingComposer)


def test_the_compose_scope_is_one_c06_refuses_to_store():
    """So enabling drafts is a deliberate, approved change - not a config flag."""
    assert GMAIL_COMPOSE in WRITE_CAPABLE_SCOPES
    from app.mailbox.scopes import REQUIRED_SCOPES, evaluate_granted_scopes

    assert GMAIL_COMPOSE not in REQUIRED_SCOPES
    decision = evaluate_granted_scopes([*REQUIRED_SCOPES, GMAIL_COMPOSE])
    assert not decision.acceptable
    assert decision.reason_code == "WRITE_SCOPE_GRANTED"
