"""Offline tests for retention planning.

`build_sweep_plan` decides what would be deleted. Deleting client data is
irreversible, so the decision is a pure function and every boundary case is
pinned here without a database.

The executor's behaviour against a real database - dry run, legal hold, and
the append-only sweep log - is verified in `CLIENT_C04_TEST_REPORT.md`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.retention import (
    MAX_RETAIN_DAYS,
    MIN_RETAIN_DAYS,
    RETENTION_TARGETS,
    UNSWEEPABLE_CLASSES,
    RetentionError,
    RetentionPolicy,
    build_sweep_plan,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
ORG = "11111111-1111-1111-1111-111111111111"


def classes(plan) -> set[str]:
    return {target.data_class for target in plan.targets}


def skipped_reason(plan, data_class: str) -> str:
    return next(reason for cls, reason in plan.skipped if cls == data_class)


def test_a_policy_produces_a_cutoff_at_the_retention_boundary() -> None:
    plan = build_sweep_plan(ORG, [RetentionPolicy("MESSAGE", 90)], now=NOW)

    target = next(t for t in plan.targets if t.data_class == "MESSAGE")
    assert target.cutoff_at == NOW - timedelta(days=90)
    assert target.table == "messages"
    assert target.timestamp_column == "sent_at"


def test_absence_of_a_policy_is_never_permission_to_delete() -> None:
    plan = build_sweep_plan(ORG, [], now=NOW)

    assert plan.targets == ()
    for data_class in RETENTION_TARGETS:
        assert skipped_reason(plan, data_class) == "no policy recorded"


def test_a_disabled_policy_deletes_nothing() -> None:
    plan = build_sweep_plan(ORG, [RetentionPolicy("MESSAGE", 30, enabled=False)], now=NOW)

    assert plan.targets == ()
    assert skipped_reason(plan, "MESSAGE") == "policy is disabled"


@pytest.mark.parametrize("data_class", sorted(UNSWEEPABLE_CLASSES))
def test_audit_and_ticket_history_are_never_swept_by_age(data_class: str) -> None:
    """Audit history is append-only; ticket deletion is a privacy workflow."""
    plan = build_sweep_plan(ORG, [RetentionPolicy(data_class, 30)], now=NOW)

    assert data_class not in classes(plan)
    assert "never swept by age" in skipped_reason(plan, data_class)


@pytest.mark.parametrize("retain_days", [0, -1, MAX_RETAIN_DAYS + 1, 100_000])
def test_out_of_range_retention_is_refused_rather_than_clamped(retain_days: int) -> None:
    """Silently clamping a bad value would delete data the client did not agree to."""
    with pytest.raises(RetentionError, match="outside the permitted"):
        build_sweep_plan(ORG, [RetentionPolicy("MESSAGE", retain_days)], now=NOW)


@pytest.mark.parametrize("retain_days", [MIN_RETAIN_DAYS, 90, MAX_RETAIN_DAYS])
def test_in_range_retention_is_accepted(retain_days: int) -> None:
    plan = build_sweep_plan(ORG, [RetentionPolicy("MESSAGE", retain_days)], now=NOW)
    assert classes(plan) == {"MESSAGE"}


def test_duplicate_policies_are_refused_as_ambiguous() -> None:
    with pytest.raises(RetentionError, match="Duplicate retention policy"):
        build_sweep_plan(
            ORG,
            [RetentionPolicy("MESSAGE", 30), RetentionPolicy("MESSAGE", 400)],
            now=NOW,
        )


def test_an_unknown_data_class_is_skipped_not_guessed() -> None:
    plan = build_sweep_plan(ORG, [RetentionPolicy("SOMETHING_NEW", 30)], now=NOW)

    assert plan.targets == ()
    assert skipped_reason(plan, "SOMETHING_NEW") == "no sweep target is defined"


def test_a_naive_timestamp_is_refused() -> None:
    """A retention cutoff computed in an unknown zone deletes the wrong rows."""
    with pytest.raises(RetentionError, match="aware timestamp"):
        build_sweep_plan(
            ORG, [RetentionPolicy("MESSAGE", 30)], now=datetime(2026, 9, 15, 12, 0)
        )


def test_every_sweep_target_names_a_real_table_and_column() -> None:
    for data_class, (table, column) in RETENTION_TARGETS.items():
        assert table.islower() and table.isidentifier(), data_class
        assert column.islower() and column.isidentifier(), data_class


def test_sweepable_and_unsweepable_classes_do_not_overlap() -> None:
    assert not (set(RETENTION_TARGETS) & UNSWEEPABLE_CLASSES)


def test_the_plan_describes_itself_for_an_operator() -> None:
    plan = build_sweep_plan(
        ORG,
        [RetentionPolicy("MESSAGE", 90), RetentionPolicy("AUDIT_EVENT", 400)],
        now=NOW,
    )
    described = plan.describe()

    assert "MESSAGE" in described
    assert "messages" in described
    assert "skipped" in described


def test_a_plan_is_immutable() -> None:
    plan = build_sweep_plan(ORG, [RetentionPolicy("MESSAGE", 90)], now=NOW)

    with pytest.raises(AttributeError):
        plan.targets = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        plan.targets[0].cutoff_at = NOW  # type: ignore[misc]
