"""Least-privilege OAuth scope policy for mailbox connections.

Two profiles, and the deployment chooses one:

* **read_only** (the default): `gmail.readonly` to read messages and register a
  Gmail watch, plus identity to prove the right mailbox was authorized. This is
  C-D007 behaviour, unchanged.
* **read_and_draft**: the same, plus `gmail.compose`, which is what lets an
  approved answer become a draft in the client's own mailbox (C12). Requested
  only where the client has authorized it, and **optional** - a person who
  unticks it on Google's consent screen still connects the mailbox read-only.

`gmail.compose` also permits sending. That is a property of Google's scope, not
a capability of this product: nothing here calls a send endpoint, a test
asserts that across the tree, and `organizations.sending_enabled` is still
constrained to false (C-D125).

A consent response is checked in both directions:

* **Missing** a required scope is a refusal. Google's granular consent lets a
  person untick boxes, so "the flow succeeded" does not mean "access was
  granted". A missing *optional* scope is not a refusal; it turns off the one
  capability that needed it.
* **Extra** scopes are a refusal. A token carrying a scope the active profile
  does not permit - `gmail.send`, `gmail.modify`, full mail access - must never
  be stored, even if the person granted it willingly, most likely through a
  previously granted scope being merged back in. The token is revoked instead.
"""

from __future__ import annotations

import os
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
OPENID = "openid"
EMAIL = "https://www.googleapis.com/auth/userinfo.email"

REQUIRED_SCOPES: frozenset[str] = frozenset({GMAIL_READONLY, OPENID, EMAIL})

PROFILE_VARIABLE = "RESOLVEFLOW_MAILBOX_SCOPES"


class ScopeProfile(StrEnum):
    READ_ONLY = "read_only"
    READ_AND_DRAFT = "read_and_draft"


# What each profile asks for beyond REQUIRED_SCOPES, and can do without.
OPTIONAL_SCOPES: dict[ScopeProfile, frozenset[str]] = {
    ScopeProfile.READ_ONLY: frozenset(),
    ScopeProfile.READ_AND_DRAFT: frozenset({GMAIL_COMPOSE}),
}

# Google returns short aliases in some responses and full URIs in others.
SCOPE_ALIASES: dict[str, str] = {
    "email": EMAIL,
    "openid": OPENID,
}

# Named so the refusal reason can say *which* dangerous capability was offered.
# Not an allowlist substitute: anything the profile does not permit is refused.
WRITE_CAPABLE_SCOPES: frozenset[str] = frozenset(
    {
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/gmail.send",
        GMAIL_COMPOSE,
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.insert",
        "https://www.googleapis.com/auth/gmail.labels",
        "https://www.googleapis.com/auth/gmail.settings.basic",
        "https://www.googleapis.com/auth/gmail.settings.sharing",
    }
)

# Refused under every profile, and mirrored by a database constraint. Drafting
# needs `gmail.compose` only; nothing in this product needs any of these, so a
# grant carrying one is a misconfigured OAuth client, not a new feature.
NEVER_PERMITTED_SCOPES: frozenset[str] = WRITE_CAPABLE_SCOPES - {GMAIL_COMPOSE}


class ScopeRefusal(Exception):
    """Granted scopes do not match the active scope policy."""

    def __init__(self, reason: str, *, missing: frozenset[str], excess: frozenset[str]):
        super().__init__(reason)
        self.reason = reason
        self.missing = missing
        self.excess = excess


@dataclass(frozen=True)
class ScopeDecision:
    granted: frozenset[str]
    missing: frozenset[str]
    excess: frozenset[str]
    profile: ScopeProfile = ScopeProfile.READ_ONLY
    # Asked for, not granted, and not required. Recorded so a later refusal to
    # create a draft can say why without re-reading the policy.
    optional_missing: frozenset[str] = frozenset()

    @property
    def acceptable(self) -> bool:
        return not self.missing and not self.excess

    @property
    def drafting(self) -> bool:
        """Whether this grant can hold a draft in the client's mailbox."""
        return drafting_available(self.granted)

    @property
    def reason_code(self) -> str | None:
        if self.excess & WRITE_CAPABLE_SCOPES:
            return "WRITE_SCOPE_GRANTED"
        if self.excess:
            return "UNEXPECTED_SCOPE_GRANTED"
        if GMAIL_READONLY in self.missing:
            return "MAILBOX_SCOPE_DENIED"
        if self.missing:
            return "IDENTITY_SCOPE_DENIED"
        return None


def active_profile() -> ScopeProfile:
    """The profile this deployment runs with. Read-only unless configured.

    Switching it is a client decision, not a deployment detail: the consent
    screen changes and the mailbox has to be re-consented by a Workspace admin
    (see docs/MAILBOX_CONNECTION_MODES.md).
    """
    raw = os.getenv(PROFILE_VARIABLE, "").strip().lower()
    if not raw:
        return ScopeProfile.READ_ONLY
    try:
        return ScopeProfile(raw)
    except ValueError:
        # An unreadable setting must never silently widen access.
        return ScopeProfile.READ_ONLY


def optional_scopes(profile: ScopeProfile | None = None) -> frozenset[str]:
    return OPTIONAL_SCOPES[profile if profile is not None else active_profile()]


def permitted_scopes(profile: ScopeProfile | None = None) -> frozenset[str]:
    """Everything the profile may hold: required plus its optional scopes."""
    return REQUIRED_SCOPES | optional_scopes(profile)


def drafting_available(granted: str | Collection[str]) -> bool:
    """Whether a grant carries the scope that creating a draft needs.

    Accepts the same shapes as `evaluate_granted_scopes`, including Google's
    space-delimited string - `list("https://...")` would otherwise split it
    into characters and answer no.
    """
    if isinstance(granted, str):
        return GMAIL_COMPOSE in normalize_scopes(granted)
    return GMAIL_COMPOSE in normalize_scopes(list(granted))


def normalize_scopes(raw: str | list[str] | tuple[str, ...] | None) -> frozenset[str]:
    """Accept Google's space-delimited string or a list, resolving aliases."""
    if raw is None:
        return frozenset()
    items = raw.split() if isinstance(raw, str) else list(raw)
    return frozenset(
        SCOPE_ALIASES.get(item.strip(), item.strip()) for item in items if item.strip()
    )


def evaluate_granted_scopes(
    raw: str | list[str] | tuple[str, ...] | None,
    profile: ScopeProfile | None = None,
) -> ScopeDecision:
    resolved = profile if profile is not None else active_profile()
    granted = normalize_scopes(raw)
    return ScopeDecision(
        granted=granted,
        missing=REQUIRED_SCOPES - granted,
        excess=granted - permitted_scopes(resolved),
        profile=resolved,
        optional_missing=optional_scopes(resolved) - granted,
    )


def require_acceptable_scopes(
    raw: str | list[str] | tuple[str, ...] | None,
    profile: ScopeProfile | None = None,
) -> frozenset[str]:
    """Return the granted set or raise `ScopeRefusal`."""
    decision = evaluate_granted_scopes(raw, profile)
    if not decision.acceptable:
        raise ScopeRefusal(
            decision.reason_code or "SCOPE_MISMATCH",
            missing=decision.missing,
            excess=decision.excess,
        )
    return decision.granted


def requested_scope_string(profile: ScopeProfile | None = None) -> str:
    """Scopes placed on the authorization URL, in a stable order."""
    return " ".join(sorted(permitted_scopes(profile)))
