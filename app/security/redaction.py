"""Redaction for anything that leaves the system as diagnostics.

Three destinations need this and one must never have it:

* **Logs, traces and error messages.** A ticket body quoted into an exception
  message ends up in a log aggregator, which has a different audience and a
  different retention period from the ticket itself.
* **Audit metadata.** An audit row is kept far longer than the data it
  describes - deliberately, so "who did what" survives erasure - which is
  exactly why it must not carry the content.
* **Operational exports** (support bundles, failure samples).
* **Never a data-subject export.** A person asking for their own data is
  entitled to it unredacted; `app.privacy` deliberately does not call this.

The patterns are ordered most specific first, because a Google refresh token
also looks like a long base64 blob, and the useful label is the specific one.

Two deliberate design choices:

* **Over-redaction is the safe failure.** A redacted log line costs an
  operator a round trip; a leaked card number costs the client an incident.
  Where a pattern is ambiguous it still redacts, and the label says what was
  suspected so the operator knows what to ask for.
* **Structure is kept.** Identifiers an operator needs - UUIDs, ticket
  references, timestamps, model names, scope URLs, HTTP status codes - are
  *not* redacted. A log line that says nothing is not a safer log line, it is
  an unusable one, and unusable logs get turned off.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

# --------------------------------------------------------------------------
# Keys whose value is never printable, whatever it looks like
# --------------------------------------------------------------------------

# Matched case-insensitively against a mapping key, as a substring: a value
# under "refresh_token_envelope" or "x-goog-api-key" must not survive because
# the name was spelled differently from the obvious one.
SECRET_KEY_HINTS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "client_secret",
        "code_verifier",
        "cookie",
        "credential",
        "envelope",
        "key_material",
        "password",
        "private",
        "refresh_token",
        "secret",
        "session",
        "set-cookie",
        "token",
        "verifier",
    }
)

# Keys an operator needs to read, which the hints above would otherwise catch.
# Each is here for a stated reason, not for convenience.
SECRET_KEY_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {
        # The *name* of a secret, never its value. C10 constrains it to a
        # Secret Manager resource path and an operator needs it to find the key.
        "credential_reference",
        # A deliberate four-character tail, already a hint rather than a key.
        "credential_hint",
        # Counts and identifiers, not material.
        "key_id",
        "token_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }
)

REDACTED: Final[str] = "[redacted]"

# --------------------------------------------------------------------------
# Value patterns
# --------------------------------------------------------------------------


def _compile() -> tuple[tuple[str, re.Pattern[str]], ...]:
    return (
        # Provider credentials, most specific first.
        # The prefixes are distinctive enough to be the signal: anything
        # starting `1//0` or `ya29.` is a Google credential by convention,
        # so the suffix length is not what makes the match safe.
        ("google-refresh-token", re.compile(r"\b1//0[A-Za-z0-9_-]{4,}")),
        ("google-access-token", re.compile(r"\bya29\.[A-Za-z0-9._-]{4,}")),
        ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
        ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{20,}")),
        ("sealed-envelope", re.compile(r"\bv1\.[A-Za-z0-9+/=_-]{24,}")),
        ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
        ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
        ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
        # Personal data.
        ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
        ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
        # Phone numbers need a leading + or separators; a bare run of digits is
        # far more often an identifier, a size, or an amount.
        ("phone", re.compile(r"\+\d[\d\s().-]{7,17}\d")),
    )


VALUE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = _compile()

# Candidate payment-card runs, confirmed with Luhn before being redacted. A
# bare 16-digit number is usually not a card, and labelling every long integer
# a card is how redaction gets switched off.
_CARD_CANDIDATE: Final[re.Pattern[str]] = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _luhn(digits: str) -> bool:
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = int(character)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def redact(value: str, *, label: bool = True) -> str:
    """Replace credentials and personal data in free text.

    `label=False` collapses everything to `[redacted]`, for destinations where
    even the *kind* of data is more than the reader needs.
    """
    if not value:
        return value

    text = value
    for name, pattern in VALUE_PATTERNS:
        text = pattern.sub(f"[{name}]" if label else REDACTED, text)

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return "[card]" if label else REDACTED
        return match.group(0)

    return _CARD_CANDIDATE.sub(card, text)


def is_secret_key(key: str) -> bool:
    """Whether a mapping key's value must never be printed."""
    lowered = key.strip().lower()
    if lowered in SECRET_KEY_EXCEPTIONS:
        return False
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def redact_value(key: str, value: Any, *, label: bool = True) -> Any:
    """Redact one mapping entry, by its key *and* by its content."""
    if is_secret_key(key):
        return REDACTED
    if isinstance(value, str):
        return redact(value, label=label)
    if isinstance(value, Mapping):
        return redact_mapping(value, label=label)
    if isinstance(value, (list, tuple)):
        kind = type(value)
        return kind(redact_value(key, item, label=label) for item in value)
    # Numbers, booleans and None carry no free text. A number that *is*
    # sensitive is sensitive because of its key, which is handled above.
    return value


def redact_mapping(mapping: Mapping[str, Any], *, label: bool = True) -> dict[str, Any]:
    """Redact every entry of a structured log record or audit metadata."""
    return {key: redact_value(key, item, label=label) for key, item in mapping.items()}


def redact_exception(error: BaseException) -> str:
    """A one-line, redacted description of a failure.

    The exception *type* is kept because it is diagnostic and carries no data.
    The message is redacted, and the chain is walked: C10 established that a
    cause can carry a credential even when the outer exception does not.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = redact(str(current)).strip()
        parts.append(f"{type(current).__name__}: {message}" if message else type(current).__name__)
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)
