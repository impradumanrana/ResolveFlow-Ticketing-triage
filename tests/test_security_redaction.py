"""Redaction of diagnostics (C13).

Two failures matter here and they pull in opposite directions. Leaking a
credential or a customer's personal data into a log aggregator is an incident.
Redacting so much that an operator cannot diagnose anything gets redaction
switched off, which is the same incident later. Every test below pins one side
or the other, and the negative controls are as important as the positive ones.
"""

from __future__ import annotations

import pytest

from app.security.redaction import (
    REDACTED,
    SECRET_KEY_EXCEPTIONS,
    SECRET_KEY_HINTS,
    is_secret_key,
    redact,
    redact_exception,
    redact_mapping,
)

# ===========================================================================
# CREDENTIALS
# ===========================================================================

CREDENTIALS = [
    ("google refresh token", "stored 1//0fAkE-ToKeN_value123 ok", "google-refresh-token"),
    ("google access token", "bearer ya29.a0AfH6SMBnotreal_x", "google-access-token"),
    ("openai key", "key sk-proj-abcdefghijklmnopqrstuv", "openai-key"),
    ("google api key", "AIzaSyA-not-a-real-key-000000000", "google-api-key"),
    ("sealed envelope", "v1.YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=", "sealed-envelope"),
    ("authorization header", "Authorization: Bearer abcd1234efgh5678", "bearer"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K", "jwt"),
    ("private key", "-----BEGIN RSA PRIVATE KEY-----", "private-key"),
]


@pytest.mark.parametrize(("name", "text", "label"), CREDENTIALS, ids=[c[0] for c in CREDENTIALS])
def test_a_credential_never_survives_redaction(name: str, text: str, label: str) -> None:
    redacted = redact(text)

    assert f"[{label}]" in redacted, redacted
    # The secret material itself is gone, not merely annotated.
    for fragment in (
        "fAkE-ToKeN_value123",
        "a0AfH6SMBnotreal_x",
        "abcdefghijklmnopqrstuv",
        "not-a-real-key",
        "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo",
        "abcd1234efgh5678",
        "dBjftJeZ4CVPmB92K",
    ):
        assert fragment not in redacted


def test_unlabelled_mode_says_nothing_about_what_was_there() -> None:
    assert redact("key sk-proj-abcdefghijklmnopqrstuv", label=False) == f"key {REDACTED}"


# ===========================================================================
# PERSONAL DATA
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "alice@example.net",
        "Alice Smith <alice.smith+tag@mail.example.co.uk>",
        "contact: BOB@EXAMPLE.ORG",
    ],
)
def test_an_email_address_is_redacted(text: str) -> None:
    redacted = redact(text)

    assert "[email]" in redacted
    assert "@" not in redacted.replace("[email]", "")


def test_a_payment_card_is_redacted() -> None:
    for card in (
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "4111111111111111",
        "5500005555555559",
        "378282246310005",
    ):
        assert redact(f"card {card} on file") == "card [card] on file", card


def test_a_long_number_that_is_not_a_card_is_left_alone() -> None:
    """The negative control. Labelling every long integer a card is how
    redaction becomes noise, and noisy redaction gets turned off."""
    for number in ("1234567890123456", "1048576000000", "20260918000100", "9999999999999"):
        assert number in redact(f"value {number}"), number


def test_a_phone_number_needs_a_country_code_to_be_redacted() -> None:
    assert redact("call +44 20 7946 0958") == "call [phone]"
    # A bare run of digits is far more often an identifier than a phone number.
    assert "0207946095" in redact("ref 0207946095")


def test_an_iban_is_redacted() -> None:
    assert "[iban]" in redact("pay to GB82WEST12345698765432")


# ===========================================================================
# WHAT AN OPERATOR STILL NEEDS TO READ
# ===========================================================================

DIAGNOSTIC = [
    "ticket RF-2026-00123",
    "9f1c2d3e-4a5b-6c7d-8e9f-0a1b2c3d4e5f",
    "2026-10-05T09:00:00Z",
    "status 429 after 2 attempts",
    "model gpt-5.6-luna cost 12500 micro-units",
    "https://www.googleapis.com/auth/gmail.readonly",
    "constraint ck_mailbox_credentials_no_send_capable_scope",
    "projects/p/secrets/rf-prod-openai-key/versions/latest",
]


@pytest.mark.parametrize("text", DIAGNOSTIC)
def test_diagnostics_survive_redaction(text: str) -> None:
    """A log line that says nothing is not a safer log line."""
    assert redact(text) == text, redact(text)


# ===========================================================================
# STRUCTURED RECORDS
# ===========================================================================


def test_a_secret_key_is_redacted_whatever_its_value_looks_like() -> None:
    record = redact_mapping({"authorization": "plain", "session": "abc", "password": "hunter2"})

    assert record == {"authorization": REDACTED, "session": REDACTED, "password": REDACTED}


@pytest.mark.parametrize("hint", sorted(SECRET_KEY_HINTS))
def test_every_declared_hint_actually_redacts(hint: str) -> None:
    assert is_secret_key(hint)
    assert is_secret_key(f"x_{hint}_y"), "hints must match as substrings, not whole keys"
    assert is_secret_key(hint.upper()), "a differently cased key is the same key"


@pytest.mark.parametrize("key", sorted(SECRET_KEY_EXCEPTIONS))
def test_every_exception_is_readable(key: str) -> None:
    """Each exception exists for a stated reason; this is the list of them."""
    assert not is_secret_key(key)


def test_nested_structures_are_redacted_throughout() -> None:
    record = redact_mapping(
        {
            "ticket_id": "RF-2026-1",
            "actor": {"membership_id": "m-1", "email": "agent@client.example"},
            "messages": [
                {"subject": "Refund to 4111111111111111"},
                {"subject": "Contact me at bob@example.net"},
            ],
            "size_bytes": 2048,
            "attempt": 2,
            "succeeded": True,
            "detail": None,
        }
    )

    assert record["ticket_id"] == "RF-2026-1"
    assert record["actor"]["membership_id"] == "m-1"
    assert record["actor"]["email"] == "[email]"
    assert record["messages"][0]["subject"] == "Refund to [card]"
    assert record["messages"][1]["subject"] == "Contact me at [email]"
    # Numbers, booleans and None are untouched: they carry no free text.
    assert record["size_bytes"] == 2048
    assert record["attempt"] == 2
    assert record["succeeded"] is True
    assert record["detail"] is None


def test_a_list_keeps_its_type() -> None:
    record = redact_mapping({"to": ("a@b.co", "c@d.co"), "cc": ["e@f.co"]})

    assert record["to"] == ("[email]", "[email]")
    assert isinstance(record["to"], tuple)
    assert record["cc"] == ["[email]"]


# ===========================================================================
# EXCEPTIONS
# ===========================================================================


def test_an_exception_chain_is_redacted_end_to_end() -> None:
    """C10's lesson: the cause carries the credential even when the wrapper
    does not, and `from None` does not detach `__context__`."""
    try:
        try:
            raise ValueError("token ya29.leaked_value_here")
        except ValueError:
            raise RuntimeError("could not reach bob@example.net") from None
    except RuntimeError as error:
        described = redact_exception(error)

    assert "ya29" not in described
    assert "bob@example.net" not in described
    assert "[google-access-token]" in described
    assert "[email]" in described
    # The types are kept: they are diagnostic and carry no data.
    assert "RuntimeError" in described and "ValueError" in described


def test_a_self_referencing_chain_terminates() -> None:
    error = RuntimeError("outer")
    error.__cause__ = error

    assert redact_exception(error) == "RuntimeError: outer"


def test_an_exception_with_no_message_still_names_its_type() -> None:
    assert redact_exception(KeyError()) == "KeyError"
