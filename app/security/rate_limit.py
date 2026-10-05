"""Rate limits on the actions worth limiting (C13).

Every mutation in this product lands in one of two places: the internal API
(Python) or the web tier's own repository (TypeScript). So the limiter is
implemented twice over **one** table and **one** policy list, and a contract
test asserts the two copies agree - the same arrangement C03 uses for roles.

Design, and why:

* **Fixed windows, not sliding.** One row per (policy, key, window), so a
  check is a single atomic upsert rather than a sorted set. The cost is that a
  caller can burst up to twice the limit across a window boundary. At these
  limits that is irrelevant, and the simplicity is what makes the control
  reviewable.
* **One atomic statement.** `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`
  increments and reads in one round trip. A read-then-write limiter does not
  limit anything under concurrency, which is the only condition that matters.
* **Fail closed.** If the limiter itself cannot run, the action is refused.
  This costs nothing: every limited action already needs the same database, so
  a deployment that cannot count is a deployment that could not have served
  the request anyway.
* **The key is hashed.** Keys are membership ids, mailbox ids and sometimes an
  address. The counter table holds a SHA-256 instead, so a control that exists
  to protect the system does not itself become a store of personal data
  (C-D009), and so counters fall outside any erasure request.
* **Windows are clock-aligned.** Two instances derive the same bucket from the
  same timestamp without coordinating.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final


class Scope(StrEnum):
    """What a limit counts per. Named so a policy cannot be misread."""

    ORGANIZATION = "ORGANIZATION"
    MEMBERSHIP = "MEMBERSHIP"
    MAILBOX = "MAILBOX"


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    limit: int
    window_seconds: int
    scope: Scope
    reason: str

    @property
    def window(self) -> timedelta:
        return timedelta(seconds=self.window_seconds)


# Each limit exists for a stated reason. A limit nobody can explain is a limit
# somebody raises the first time it fires.
POLICIES: Final[dict[str, RateLimitPolicy]] = {
    policy.name: policy
    for policy in (
        RateLimitPolicy(
            name="internal_api",
            limit=600,
            window_seconds=60,
            scope=Scope.ORGANIZATION,
            reason=(
                "A ceiling on the whole private API. A loop in the web tier is the "
                "realistic cause, and this bounds the database load it can cause."
            ),
        ),
        RateLimitPolicy(
            name="review_decision",
            limit=60,
            window_seconds=60,
            scope=Scope.MEMBERSHIP,
            reason=(
                "A person reviewing conversations works in seconds, not milliseconds. "
                "Sixty a minute is far above human pace and far below a script's."
            ),
        ),
        RateLimitPolicy(
            name="bulk_assign",
            limit=10,
            window_seconds=60,
            scope=Scope.MEMBERSHIP,
            reason=(
                "Each call already moves up to the bulk maximum, so ten a minute is "
                "ample for triage and bounds how fast one account can reassign a queue."
            ),
        ),
        RateLimitPolicy(
            name="ai_settings_write",
            limit=20,
            window_seconds=3600,
            scope=Scope.ORGANIZATION,
            reason=(
                "Changing models, budgets or keys is a deliberate act. A burst of them "
                "is either a mistake or someone probing, and both deserve a pause."
            ),
        ),
        RateLimitPolicy(
            name="credential_verify",
            limit=10,
            window_seconds=3600,
            scope=Scope.ORGANIZATION,
            reason=(
                "Verification calls a paid provider with a supplied key. Limiting it "
                "bounds both spend and use of this service as a key-testing oracle."
            ),
        ),
        RateLimitPolicy(
            name="mailbox_connect",
            limit=5,
            window_seconds=3600,
            scope=Scope.MAILBOX,
            reason=(
                "Connecting is a human flow through Google's consent screen. Repeated "
                "attempts mean something is wrong, and each one mints a grant."
            ),
        ),
        RateLimitPolicy(
            name="draft_preview",
            limit=120,
            window_seconds=60,
            scope=Scope.MEMBERSHIP,
            reason=(
                "A read, but one that renders a full reply. Generous enough for a page "
                "that refreshes, tight enough not to be a free amplifier."
            ),
        ),
    )
}

TABLE: Final[str] = "rate_limit_counters"

# Counters are evidence of nothing after a few windows. The retention sweep
# removes them; this is how long one is useful for diagnosing a burst.
COUNTER_RETENTION = timedelta(days=2)


class RateLimitExceeded(Exception):
    """The caller has used its allowance for this window."""

    def __init__(self, policy: RateLimitPolicy, *, retry_after_seconds: int):
        super().__init__(policy.name)
        self.policy = policy
        self.code = "RATE_LIMITED"
        self.retry_after_seconds = retry_after_seconds


class RateLimitUnavailable(Exception):
    """The limiter could not run, so the action is refused (fail closed)."""

    def __init__(self, policy: RateLimitPolicy):
        super().__init__(policy.name)
        self.policy = policy
        self.code = "RATE_LIMIT_UNAVAILABLE"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def window_start(moment: datetime, window_seconds: int) -> datetime:
    """The clock-aligned bucket a moment belongs to.

    Aligned to the epoch rather than to first use, so every instance and both
    language runtimes derive the same bucket from the same timestamp.
    """
    if moment.tzinfo is None:
        raise ValueError("rate limit windows need an aware timestamp")
    epoch_seconds = int(moment.astimezone(UTC).timestamp())
    return datetime.fromtimestamp(epoch_seconds - (epoch_seconds % window_seconds), tz=UTC)


def hash_key(value: str) -> str:
    """A counter is keyed by a digest, never by the identifier itself."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def retry_after(policy: RateLimitPolicy, moment: datetime) -> int:
    """Whole seconds until this caller's allowance resets."""
    nxt = window_start(moment, policy.window_seconds) + policy.window
    return max(1, int((nxt - moment).total_seconds() + 0.999))


class PostgresRateLimiter:
    """Counts in one statement, in its own short transaction.

    The engine is used directly rather than an outer transaction, because a
    limiter that rolls back with the work it was limiting does not limit a
    retry loop.
    """

    def __init__(self, engine: Any, *, now: Any = _utc_now):
        self._engine = engine
        self._now = now

    def check(self, policy_name: str, key: str, *, organization_id: str) -> int:
        policy = POLICIES[policy_name]
        moment = self._now()
        start = window_start(moment, policy.window_seconds)

        from sqlalchemy import text

        try:
            with self._engine.begin() as connection:
                count = connection.execute(
                    text(
                        f"INSERT INTO {TABLE} "  # noqa: S608 - TABLE is a module constant
                        "(organization_id, policy, key_hash, window_start, request_count) "
                        "VALUES (CAST(:organization AS uuid), :policy, :key, :start, 1) "
                        "ON CONFLICT (organization_id, policy, key_hash, window_start) "
                        "DO UPDATE SET request_count = "
                        f"{TABLE}.request_count + 1, updated_at = now() "
                        "RETURNING request_count"
                    ),
                    {
                        "organization": organization_id,
                        "policy": policy.name,
                        "key": hash_key(key),
                        "start": start,
                    },
                ).scalar_one()
        except RateLimitExceeded:  # pragma: no cover - defensive
            raise
        except Exception as error:
            raise RateLimitUnavailable(policy) from error

        if count > policy.limit:
            raise RateLimitExceeded(policy, retry_after_seconds=retry_after(policy, moment))
        return int(count)


class InMemoryRateLimiter:
    """The same arithmetic without a database, for tests and local runs."""

    def __init__(self, *, now: Any = _utc_now):
        self._now = now
        self.counts: dict[tuple[str, str, str, datetime], int] = {}

    def check(self, policy_name: str, key: str, *, organization_id: str) -> int:
        policy = POLICIES[policy_name]
        moment = self._now()
        bucket = (
            organization_id,
            policy.name,
            hash_key(key),
            window_start(moment, policy.window_seconds),
        )
        count = self.counts.get(bucket, 0) + 1
        self.counts[bucket] = count
        if count > policy.limit:
            raise RateLimitExceeded(policy, retry_after_seconds=retry_after(policy, moment))
        return count
