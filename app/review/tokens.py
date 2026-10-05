"""Short-lived mailbox access tokens for creating a draft.

C12 needs a bearer token for one call: `POST /gmail/v1/users/me/drafts`. The
refresh token that mints it lives sealed in C06's vault and is never handled
here; this module asks C06's `ConnectionService.refresh` and passes on only the
access token it returns.

Three properties matter:

* **Nothing is stored.** The token is held in memory for at most its cache
  window and never reaches a column, a log line, a trace, or an export
  (C-D009). `_CachedToken` is unprintable for the same reason `SecretValue` is.
* **The refusal is C06's.** A revoked mailbox, an unreadable credential, a
  removed scope - each already has a code and already marks the mailbox
  degraded or withdrawn. Repeating that judgement here would let the two drift
  apart, so a failed refresh becomes `TokenUnavailable(code)` and the review
  records `MAILBOX_TOKEN_UNAVAILABLE` against the original code.
* **One short transaction per call.** The refresh opens the vault and writes
  `last_refreshed_at`; the Gmail call happens afterwards, outside it, so a slow
  provider never holds a row lock (the C10 lesson).

The cache window is 45 minutes against Google's hour: a token that expires
mid-call fails the draft, and a review is interactive, so a person would see
it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# Shorter than Google's hour, by the same margin C07 uses for ingestion.
ACCESS_TOKEN_TTL = timedelta(minutes=45)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TokenUnavailable(Exception):
    """No access token for this mailbox. Carries C06's reason code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class _CachedToken:
    # repr=False, and the class is never logged: a token in a traceback is a
    # token in a log aggregator.
    access_token: str = field(repr=False)
    expires_at: datetime


class RefreshingTokenProvider:
    """Calls C06 for a token and reuses it until it is nearly expired.

    Held per process. Two API workers each keep their own, which is correct:
    the token is Google's to invalidate, not ours to synchronise.
    """

    def __init__(
        self,
        refresh: Callable[[str], object],
        *,
        now: Callable[[], datetime] = _utc_now,
        ttl: timedelta = ACCESS_TOKEN_TTL,
    ):
        self._refresh = refresh
        self._now = now
        self._ttl = ttl
        self._cache: dict[str, _CachedToken] = {}

    def __call__(self, mailbox_id: str) -> str:
        cached = self._cache.get(mailbox_id)
        if cached is not None and cached.expires_at > self._now():
            return cached.access_token

        # A stale entry goes before the call, not after: if the refresh fails
        # the next attempt must not find an expired token still sitting here.
        self._cache.pop(mailbox_id, None)

        outcome = self._refresh(mailbox_id)
        ok = bool(getattr(outcome, "ok", False))
        code = str(getattr(outcome, "code", "") or "MAILBOX_TOKEN_UNAVAILABLE")
        token = getattr(outcome, "access_token", None)

        if not ok or not isinstance(token, str) or not token:
            # `ok` with no token would be a C06 bug; treated as a failure
            # rather than handed on as an empty bearer.
            raise TokenUnavailable(code if not ok else "MAILBOX_TOKEN_MISSING")

        self._cache[mailbox_id] = _CachedToken(
            access_token=token, expires_at=self._now() + self._ttl
        )
        return token

    def invalidate(self, mailbox_id: str) -> None:
        """Drop a cached token, after a 401 from the provider."""
        self._cache.pop(mailbox_id, None)
