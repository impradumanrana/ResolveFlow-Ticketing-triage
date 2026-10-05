"""Rate limits (C13).

The limiter exists twice - once per tier that writes - over one table and one
policy list. These tests hold the arithmetic, the fail-closed behaviour, the
API's refusal shape, and the agreement between the two copies. The last one
matters most: two tiers with different numbers is two different limits, and
nobody would notice until one of them let something through.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.security.rate_limit import (
    COUNTER_RETENTION,
    POLICIES,
    TABLE,
    InMemoryRateLimiter,
    PostgresRateLimiter,
    RateLimitExceeded,
    RateLimitPolicy,
    RateLimitUnavailable,
    Scope,
    hash_key,
    retry_after,
    window_start,
)

ROOT = Path(__file__).resolve().parents[1]
ORG = "11111111-1111-1111-1111-111111111111"
OTHER_ORG = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


# ===========================================================================
# THE POLICIES
# ===========================================================================


@pytest.mark.parametrize("name", sorted(POLICIES))
def test_every_policy_is_well_formed(name: str) -> None:
    policy = POLICIES[name]

    assert policy.name == name, "a policy's key and name must agree"
    assert policy.limit > 0
    assert policy.window_seconds > 0
    assert isinstance(policy.scope, Scope)
    # A limit nobody can explain is a limit somebody raises the first time it
    # fires, so each one states why it is where it is.
    assert len(policy.reason) > 40, f"{name} has no stated reason"
    assert policy.window == timedelta(seconds=policy.window_seconds)


def test_the_policies_cover_every_mutating_surface() -> None:
    """A new mutating route without a limit should be a visible omission."""
    assert set(POLICIES) == {
        "internal_api",
        "review_decision",
        "bulk_assign",
        "ai_settings_write",
        "credential_verify",
        "mailbox_connect",
        "draft_preview",
    }


def test_the_limits_are_above_human_pace_and_below_a_scripts() -> None:
    """A limit that fires during ordinary work gets raised until it is useless."""
    per_minute = {
        name: policy.limit * 60 / policy.window_seconds for name, policy in POLICIES.items()
    }
    # Nothing a person does by hand approaches these.
    assert per_minute["review_decision"] >= 30
    assert per_minute["bulk_assign"] >= 5
    # And nothing is so generous that it stops being a limit.
    assert all(rate <= 600 for rate in per_minute.values()), per_minute


# ===========================================================================
# WINDOWS AND KEYS
# ===========================================================================


def test_a_window_is_aligned_to_the_clock_not_to_first_use() -> None:
    """Two instances must derive the same bucket from the same timestamp."""
    assert window_start(datetime(2026, 10, 5, 9, 0, 37, tzinfo=UTC), 60) == datetime(
        2026, 10, 5, 9, 0, tzinfo=UTC
    )
    assert window_start(datetime(2026, 10, 5, 9, 0, 2, tzinfo=UTC), 60) == window_start(
        datetime(2026, 10, 5, 9, 0, 59, tzinfo=UTC), 60
    )
    assert window_start(datetime(2026, 10, 5, 9, 42, 13, tzinfo=UTC), 3600) == datetime(
        2026, 10, 5, 9, 0, tzinfo=UTC
    )


def test_a_naive_timestamp_is_refused() -> None:
    """A window derived from an ambiguous local time is not a window."""
    with pytest.raises(ValueError):
        window_start(datetime(2026, 10, 5, 9, 0), 60)  # noqa: DTZ001 - the point of the test


def test_a_non_utc_timestamp_lands_in_the_same_bucket() -> None:
    from datetime import timezone

    moment = datetime(2026, 10, 5, 9, 0, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    assert window_start(moment, 3600) == window_start(moment.astimezone(UTC), 3600)


def test_the_key_is_hashed_so_the_table_holds_no_identifier() -> None:
    digest = hash_key("m-0191c4aa-agent")

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert "agent" not in digest
    # Normalised, so the same caller is one bucket however the key is cased.
    assert hash_key("  M-0191C4AA-AGENT ") == digest
    assert hash_key("someone@acme.example") != digest


def test_retry_after_is_whole_seconds_and_never_zero() -> None:
    policy = POLICIES["bulk_assign"]

    assert retry_after(policy, datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)) == 60
    assert retry_after(policy, datetime(2026, 10, 5, 9, 0, 59, 500000, tzinfo=UTC)) == 1
    assert retry_after(policy, datetime(2026, 10, 5, 9, 0, 59, 999999, tzinfo=UTC)) >= 1


# ===========================================================================
# COUNTING
# ===========================================================================


def test_the_allowance_is_spent_exactly_once() -> None:
    clock = Clock()
    limiter = InMemoryRateLimiter(now=clock)
    policy = POLICIES["bulk_assign"]

    for expected in range(1, policy.limit + 1):
        assert limiter.check(policy.name, "m-1", organization_id=ORG) == expected

    with pytest.raises(RateLimitExceeded) as refused:
        limiter.check(policy.name, "m-1", organization_id=ORG)
    assert refused.value.policy is policy
    assert refused.value.code == "RATE_LIMITED"
    assert refused.value.retry_after_seconds == 60


def test_a_new_window_restores_the_allowance() -> None:
    clock = Clock()
    limiter = InMemoryRateLimiter(now=clock)
    policy = POLICIES["bulk_assign"]

    for _ in range(policy.limit):
        limiter.check(policy.name, "m-1", organization_id=ORG)
    clock.advance(seconds=policy.window_seconds)

    assert limiter.check(policy.name, "m-1", organization_id=ORG) == 1


@pytest.mark.parametrize(
    ("label", "first", "second"),
    [
        ("different keys", ("m-1", ORG), ("m-2", ORG)),
        ("different organizations", ("m-1", ORG), ("m-1", OTHER_ORG)),
    ],
    ids=["keys", "organizations"],
)
def test_allowances_are_separate(
    label: str, first: tuple[str, str], second: tuple[str, str]
) -> None:
    limiter = InMemoryRateLimiter(now=Clock())
    policy = POLICIES["bulk_assign"]

    for _ in range(policy.limit):
        limiter.check(policy.name, first[0], organization_id=first[1])

    assert limiter.check(policy.name, second[0], organization_id=second[1]) == 1, label


def test_policies_do_not_share_a_counter() -> None:
    limiter = InMemoryRateLimiter(now=Clock())

    limiter.check("bulk_assign", "m-1", organization_id=ORG)

    assert limiter.check("review_decision", "m-1", organization_id=ORG) == 1


class CountingEngine:
    """An engine whose upsert returns a rising count, like the real one.

    The in-memory limiter has its own arithmetic, so driving only that leaves
    the statement-backed limiter's boundary untested - which is how an
    off-by-one in `count > limit` survived a mutation run.
    """

    def __init__(self) -> None:
        self.count = 0

    def begin(self):  # noqa: ANN201 - a context-manager stand-in
        engine = self

        class Transaction:
            def __enter__(self) -> Transaction:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def execute(self, statement: object, parameters: dict[str, object]):  # noqa: ANN202
                engine.count += 1

                class Result:
                    def scalar_one(inner_self) -> int:
                        return engine.count

                return Result()

        return Transaction()


def test_the_statement_backed_limiter_spends_the_allowance_exactly_once() -> None:
    """The boundary, on the limiter that production actually uses."""
    limiter = PostgresRateLimiter(CountingEngine(), now=Clock())
    policy = POLICIES["bulk_assign"]

    for expected in range(1, policy.limit + 1):
        assert limiter.check(policy.name, "m-1", organization_id=ORG) == expected

    with pytest.raises(RateLimitExceeded):
        limiter.check(policy.name, "m-1", organization_id=ORG)


def test_the_statement_backed_limiter_allows_the_last_request_in_the_allowance() -> None:
    """The other side of the boundary: refusing at the limit rather than above
    it would quietly cost every caller one request."""
    limiter = PostgresRateLimiter(CountingEngine(), now=Clock())
    policy = POLICIES["review_decision"]

    for _ in range(policy.limit - 1):
        limiter.check(policy.name, "m-1", organization_id=ORG)

    assert limiter.check(policy.name, "m-1", organization_id=ORG) == policy.limit


# ===========================================================================
# FAIL CLOSED
# ===========================================================================


def test_a_limiter_that_cannot_count_refuses_the_request() -> None:
    """Every limited action already needs this database, so refusing costs no
    availability that was not already gone."""

    class BrokenEngine:
        def begin(self) -> None:
            raise RuntimeError("connection refused")

    limiter = PostgresRateLimiter(BrokenEngine(), now=Clock())

    with pytest.raises(RateLimitUnavailable) as refused:
        limiter.check("review_decision", "m-1", organization_id=ORG)
    assert refused.value.code == "RATE_LIMIT_UNAVAILABLE"
    assert refused.value.policy is POLICIES["review_decision"]


def test_the_two_refusals_are_distinguishable() -> None:
    """An operator has to tell a busy client from a broken counter."""
    assert RateLimitExceeded(POLICIES["bulk_assign"], retry_after_seconds=1).code != (
        RateLimitUnavailable(POLICIES["bulk_assign"]).code
    )


def test_the_counter_failure_does_not_carry_the_cause_outward() -> None:
    """A database error can quote a connection string."""

    class LeakyEngine:
        def begin(self) -> None:
            raise RuntimeError("password=hunter2 host=db.internal")

    limiter = PostgresRateLimiter(LeakyEngine(), now=Clock())

    with pytest.raises(RateLimitUnavailable) as refused:
        limiter.check("review_decision", "m-1", organization_id=ORG)
    assert "hunter2" not in str(refused.value)
    assert str(refused.value) == "review_decision"


# ===========================================================================
# THE STATEMENT
# ===========================================================================


def test_counting_is_one_atomic_statement() -> None:
    """A read-then-write limiter does not limit anything under concurrency."""
    captured: dict[str, str] = {}

    class RecordingEngine:
        def begin(self):  # noqa: ANN202 - a context manager stand-in
            class Transaction:
                def __enter__(self) -> Transaction:
                    return self

                def __exit__(self, *_: object) -> None:
                    return None

                def execute(self, statement: object, parameters: dict[str, object]):  # noqa: ANN202
                    captured["sql"] = str(statement)
                    captured["key"] = str(parameters["key"])

                    class Result:
                        def scalar_one(self) -> int:
                            return 1

                    return Result()

            return Transaction()

    PostgresRateLimiter(RecordingEngine(), now=Clock()).check(
        "review_decision", "m-1", organization_id=ORG
    )

    sql = captured["sql"]
    assert sql.count("INSERT INTO") == 1
    assert "ON CONFLICT" in sql
    assert "request_count + 1" in sql
    assert "RETURNING request_count" in sql
    assert "SELECT" not in sql, "a separate read would make the count a race"
    # And the identifier never reaches the statement.
    assert captured["key"] == hash_key("m-1")
    assert "m-1" not in captured["key"]


def test_the_table_the_limiter_writes_is_the_one_the_migration_creates() -> None:
    migration = (ROOT / "migrations/versions/20260919_0011_rate_limits.py").read_text()

    assert f'"{TABLE}"' in migration
    assert COUNTER_RETENTION > timedelta(0)


# ===========================================================================
# THE TWO TIERS AGREE
# ===========================================================================


def test_the_web_tier_mirrors_the_policies_it_enforces() -> None:
    """Two runtimes, one source of truth, one test that fails if they drift."""
    source = (ROOT / "apps/web/src/lib/security/rate-limit.ts").read_text()

    mirrored = {
        match.group("name"): RateLimitPolicy(
            name=match.group("name"),
            limit=int(match.group("limit")),
            window_seconds=int(match.group("window")),
            scope=Scope(match.group("scope")),
            reason="mirrored",
        )
        for match in re.finditer(
            r'name: "(?P<name>\w+)",\s*\n\s*limit: (?P<limit>\d+),\s*\n\s*'
            r'windowSeconds: (?P<window>\d+),\s*\n\s*scope: "(?P<scope>\w+)"',
            source,
        )
    }

    assert mirrored, "no policies parsed from the web tier; the mirror test is broken"
    for name, policy in mirrored.items():
        assert name in POLICIES, f"the web tier enforces {name}, which Python does not define"
        theirs = POLICIES[name]
        assert policy.limit == theirs.limit, name
        assert policy.window_seconds == theirs.window_seconds, name
        assert policy.scope == theirs.scope, name


def test_the_web_tier_enforces_the_limit_for_what_it_writes_itself() -> None:
    """Bulk assignment writes from the web tier, so the web tier limits it."""
    action = (ROOT / "apps/web/src/app/workspace/actions.ts").read_text()

    assert "enforceRateLimit" in action
    assert '"bulk_assign"' in action
    assert "RateLimitExceededError" in action
    # And it must happen after the permission check, not instead of it.
    assert action.index("requireAccess") < action.index("enforceRateLimit")


def test_the_web_tier_hashes_its_keys_too() -> None:
    limiter = (ROOT / "apps/web/src/lib/security/limiter.server.ts").read_text()

    assert "createHash" in limiter and "sha256" in limiter
    assert "hashKey(key)" in limiter
