"""Push notification parsing, history folding, and message normalization.

All pure, all offline. These are the layers that first touch untrusted input:
a Pub/Sub envelope anyone inside the project could publish, and an email body
written by a stranger.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest

from app.ingestion.history import advance_cursor, compute_effects, is_behind
from app.ingestion.normalize import (
    ALLOWED_ATTACHMENT_TYPES,
    MAX_ATTACHMENT_BYTES,
    MAX_BODY_CHARS,
    NormalizationError,
    clean_filename,
    clean_header,
    normalize_address,
    normalize_addresses,
    normalize_message,
)
from app.ingestion.pubsub import (
    NotificationRefused,
    notification_job_key,
    parse_push_envelope,
)


def envelope(payload: dict | None = None, *, message_id: str = "9876", data: str | None = None):
    encoded = data
    if encoded is None:
        encoded = base64.b64encode(json.dumps(payload or {}).encode()).decode()
    return {"message": {"data": encoded, "messageId": message_id}, "subscription": "sub"}


def b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


# ===========================================================================
# Push notifications
# ===========================================================================


def test_a_well_formed_notification_is_parsed() -> None:
    notification = parse_push_envelope(
        envelope({"emailAddress": "Support@Acme.example", "historyId": "4242"})
    )

    assert notification.email_address == "support@acme.example"
    assert notification.history_id == "4242"
    assert notification.history_id_int == 4242
    assert notification_job_key(notification) == "9876"


def test_the_deduplication_key_is_the_delivery_id_alone() -> None:
    """Including the history id would let a restated notification through."""
    first = parse_push_envelope(envelope({"emailAddress": "a@b.example", "historyId": "1"}))
    second = parse_push_envelope(envelope({"emailAddress": "a@b.example", "historyId": "2"}))

    assert notification_job_key(first) == notification_job_key(second)


def test_unpadded_base64_is_accepted() -> None:
    raw = json.dumps({"emailAddress": "a@b.example", "historyId": "7"}).encode()
    stripped = base64.b64encode(raw).decode().rstrip("=")

    assert parse_push_envelope(envelope(data=stripped)).history_id == "7"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("not a dict", "ENVELOPE_NOT_AN_OBJECT"),
        ({}, "MESSAGE_MISSING"),
        ({"message": "text"}, "MESSAGE_MISSING"),
        ({"message": {"data": "x"}}, "MESSAGE_ID_MISSING"),
        ({"message": {"messageId": "1"}}, "DATA_MISSING"),
        ({"message": {"messageId": "1", "data": "!!!not base64!!!"}}, "DATA_NOT_BASE64"),
    ],
)
def test_malformed_envelopes_are_refused(body, code: str) -> None:
    with pytest.raises(NotificationRefused) as refused:
        parse_push_envelope(body)
    assert refused.value.code == code


def test_a_message_id_that_could_not_deduplicate_is_refused() -> None:
    with pytest.raises(NotificationRefused) as refused:
        parse_push_envelope(
            envelope({"emailAddress": "a@b.example", "historyId": "1"}, message_id="x" * 300)
        )
    assert refused.value.code == "MESSAGE_ID_MISSING"


def test_valid_base64_that_is_not_json_is_refused() -> None:
    with pytest.raises(NotificationRefused) as refused:
        parse_push_envelope(envelope(data=base64.b64encode(b"plain text").decode()))
    assert refused.value.code == "DATA_NOT_JSON"


def test_a_payload_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(NotificationRefused) as refused:
        parse_push_envelope(envelope(data=base64.b64encode(b"[1,2,3]").decode()))
    assert refused.value.code == "DATA_NOT_AN_OBJECT"


def test_an_oversized_payload_is_refused_before_parsing() -> None:
    huge = base64.b64encode(json.dumps({"pad": "x" * 100_000}).encode()).decode()
    with pytest.raises(NotificationRefused) as refused:
        parse_push_envelope(envelope(data=huge))
    assert refused.value.code == "DATA_TOO_LARGE"


@pytest.mark.parametrize(
    "address", ["", "no-at-sign", "two@at@signs.example", "has space@x.example", None]
)
def test_an_unusable_mailbox_address_is_refused(address) -> None:
    with pytest.raises(NotificationRefused) as refused:
        parse_push_envelope(envelope({"emailAddress": address, "historyId": "1"}))
    assert refused.value.code == "EMAIL_ADDRESS_INVALID"


@pytest.mark.parametrize("history_id", ["", "abc", "-1", "12.5", "1" * 30, None])
def test_an_unusable_history_id_is_refused(history_id) -> None:
    with pytest.raises(NotificationRefused) as refused:
        parse_push_envelope(envelope({"emailAddress": "a@b.example", "historyId": history_id}))
    assert refused.value.code == "HISTORY_ID_INVALID"


# ===========================================================================
# History folding
# ===========================================================================


def added(history_id: str, *message_ids: str) -> dict:
    return {"id": history_id, "messagesAdded": [{"message": {"id": m}} for m in message_ids]}


def removed(history_id: str, *message_ids: str) -> dict:
    return {"id": history_id, "messagesDeleted": [{"message": {"id": m}} for m in message_ids]}


def test_additions_are_collected_in_order_without_duplicates() -> None:
    effects = compute_effects([added("10", "m1", "m2"), added("11", "m2", "m3")])

    assert effects.added_message_ids == ("m1", "m2", "m3")
    assert effects.highest_history_id == "11"


def test_a_message_deleted_in_the_same_window_is_not_fetched() -> None:
    """Fetching it would fail; treating it as deleted is correct and cheaper."""
    effects = compute_effects([added("10", "m1"), removed("11", "m1")])

    assert effects.added_message_ids == ()
    assert effects.deleted_message_ids == ("m1",)


def test_deletion_wins_regardless_of_record_order() -> None:
    effects = compute_effects([removed("11", "m1"), added("10", "m1")])
    assert effects.added_message_ids == ()
    assert effects.deleted_message_ids == ("m1",)


def test_malformed_records_are_skipped_rather_than_crashing() -> None:
    effects = compute_effects(
        ["text", {}, {"id": "x"}, {"messagesAdded": "nope"}, added("5", "m1")]
    )

    assert effects.added_message_ids == ("m1",)
    assert effects.highest_history_id == "5"


def test_empty_history_is_empty() -> None:
    assert compute_effects([]).is_empty


@pytest.mark.parametrize(
    ("current", "observed", "expected"),
    [
        ("100", "90", "100"),
        ("100", "120", "120"),
        (None, "50", "50"),
        ("100", None, "100"),
        ("100", "abc", "100"),
        (None, None, None),
    ],
)
def test_the_cursor_never_moves_backwards(current, observed, expected) -> None:
    assert advance_cursor(current, observed) == expected


def test_is_behind_detects_only_genuinely_newer_positions() -> None:
    assert is_behind("100", "101")
    assert is_behind(None, "1")
    assert not is_behind("100", "100")
    assert not is_behind("100", "99")
    assert not is_behind("100", None)


# ===========================================================================
# Message normalization
# ===========================================================================


def resource(**overrides):
    base = {
        "id": "m1",
        "threadId": "t1",
        "internalDate": "1789552800000",
        "snippet": "Customer needs help",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Customer <customer@example.net>"},
                {"name": "To", "value": "support@acme.example"},
                {"name": "Subject", "value": "Cannot reset password"},
                {"name": "Message-ID", "value": "<abc@example.net>"},
            ],
            "parts": [{"mimeType": "text/plain", "body": {"data": b64url("Please help.")}}],
        },
    }
    base.update(overrides)
    return base


def test_a_plain_message_normalizes() -> None:
    message = normalize_message(resource(), mailbox_address="support@acme.example")

    assert message.direction == "INBOUND"
    assert message.from_address == "customer@example.net"
    assert message.subject == "Cannot reset password"
    assert message.body_text == "Please help."
    assert message.sent_at == datetime(2026, 9, 16, 10, 0, tzinfo=UTC)


def test_time_comes_from_gmail_not_the_date_header() -> None:
    """The Date header is sender-controlled; ordering and SLA cannot use it."""
    payload = resource()
    payload["payload"]["headers"].append(
        {"name": "Date", "value": "Tue, 1 Jan 1980 00:00:00 +0000"}
    )

    assert normalize_message(payload, mailbox_address="support@acme.example").sent_at.year == 2026


def test_a_message_from_the_mailbox_itself_is_outbound() -> None:
    payload = resource()
    payload["payload"]["headers"][0] = {"name": "From", "value": "support@acme.example"}

    assert (
        normalize_message(payload, mailbox_address="support@acme.example").direction == "OUTBOUND"
    )


def test_a_missing_internal_date_is_refused() -> None:
    payload = resource()
    del payload["internalDate"]

    with pytest.raises(NormalizationError, match="internalDate"):
        normalize_message(payload, mailbox_address="support@acme.example")


@pytest.mark.parametrize("missing", ["id", "threadId"])
def test_a_message_without_identifiers_is_refused(missing: str) -> None:
    payload = resource()
    del payload[missing]

    with pytest.raises(NormalizationError):
        normalize_message(payload, mailbox_address="support@acme.example")


def test_header_injection_is_stripped() -> None:
    assert (
        clean_header("Subject\r\nBcc: attacker@evil.example")
        == "Subject Bcc: attacker@evil.example"
    )
    assert "\n" not in (clean_header("a\nb") or "")
    assert clean_header("\x00\x07evil") == "evil"
    assert clean_header("   ") is None


def test_an_absurd_subject_is_truncated() -> None:
    assert len(clean_header("x" * 5000) or "") == 998


def test_addresses_are_parsed_lowercased_and_bounded() -> None:
    assert normalize_address("Customer <Customer@Example.net>") == "customer@example.net"
    assert normalize_address("not-an-address") is None
    assert normalize_addresses("a@x.example, B <b@x.example>, a@x.example") == (
        "a@x.example",
        "b@x.example",
    )
    assert normalize_addresses(None) == ()


def test_a_recipient_list_is_capped() -> None:
    many = ", ".join(f"user{i}@x.example" for i in range(500))
    assert len(normalize_addresses(many)) == 100


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("../../etc/passwd", "etc/passwd".split("/")[-1]),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("report.pdf", "report.pdf"),
        ("", "attachment"),
        (None, "attachment"),
        (".hidden", "hidden"),
        ("with\nnewline.pdf", "withnewline.pdf"),
    ],
)
def test_attachment_filenames_cannot_traverse_a_path(given, expected: str) -> None:
    assert clean_filename(given) == expected


def test_an_html_only_body_is_converted_to_text_without_markup() -> None:
    payload = resource()
    payload["payload"]["parts"] = [
        {
            "mimeType": "text/html",
            "body": {"data": b64url("<style>p{color:red}</style><p>Hello</p><script>x()</script>")},
        }
    ]
    body = normalize_message(payload, mailbox_address="support@acme.example").body_text

    assert body is not None and "Hello" in body
    assert "color:red" not in body and "x()" not in body


def test_plain_text_is_preferred_over_html() -> None:
    payload = resource()
    payload["payload"]["parts"] = [
        {"mimeType": "text/html", "body": {"data": b64url("<p>html version</p>")}},
        {"mimeType": "text/plain", "body": {"data": b64url("plain version")}},
    ]

    assert (
        normalize_message(payload, mailbox_address="support@acme.example").body_text
        == "plain version"
    )


def test_an_enormous_body_is_truncated_with_a_marker() -> None:
    payload = resource()
    payload["payload"]["parts"] = [
        {"mimeType": "text/plain", "body": {"data": b64url("x" * 400_000)}}
    ]
    body = normalize_message(payload, mailbox_address="support@acme.example").body_text

    assert body is not None
    assert len(body) <= MAX_BODY_CHARS
    assert body.endswith("[truncated by ResolveFlow]")


def test_undecodable_body_data_does_not_crash() -> None:
    payload = resource()
    payload["payload"]["parts"] = [{"mimeType": "text/plain", "body": {"data": "!!!!"}}]

    assert normalize_message(payload, mailbox_address="support@acme.example").body_text is None


def test_allowed_attachments_are_marked_for_scanning() -> None:
    payload = resource()
    payload["payload"]["parts"].append(
        {
            "mimeType": "application/pdf",
            "filename": "invoice.pdf",
            "body": {"attachmentId": "a1", "size": 2048},
        }
    )
    message = normalize_message(payload, mailbox_address="support@acme.example")

    assert message.has_attachments
    assert message.attachments[0].scan_state == "PENDING"
    assert message.attachments[0].skip_reason is None


@pytest.mark.parametrize(
    ("mime", "size", "reason"),
    [
        ("application/x-msdownload", 100, "TYPE_NOT_ALLOWED"),
        ("application/pdf", MAX_ATTACHMENT_BYTES + 1, "TOO_LARGE"),
        ("application/pdf", 0, "EMPTY"),
    ],
)
def test_attachments_outside_policy_are_never_fetched(mime: str, size: int, reason: str) -> None:
    payload = resource()
    payload["payload"]["parts"].append(
        {"mimeType": mime, "filename": "thing", "body": {"attachmentId": "a1", "size": size}}
    )
    attachment = normalize_message(payload, mailbox_address="support@acme.example").attachments[0]

    assert attachment.scan_state == "SKIPPED"
    assert attachment.skip_reason == reason


def test_no_executable_type_is_on_the_allowed_list() -> None:
    for mime in ALLOWED_ATTACHMENT_TYPES:
        assert "msdownload" not in mime and "executable" not in mime
