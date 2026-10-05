"""C09 gate: time zones and service levels.

Every expected value here was worked out by hand from the calendar, not
copied from the code's output. Where a case exists because of a specific
trap, the test name says which.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.rules.business_hours import (
    BusinessCalendar,
    BusinessHoursError,
    add_business_minutes,
    business_minutes_between,
    next_business_start,
)
from app.rules.sla import (
    AT_RISK_MINUTES,
    SlaConflict,
    SlaPolicy,
    SlaTargets,
    TicketScope,
    compute_targets,
    evaluate_status,
    select_policy,
)

LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
KOLKATA = ZoneInfo("Asia/Kolkata")


def london(*args: int) -> datetime:
    return datetime(*args, tzinfo=LONDON)


def calendar(zone: str = "Europe/London", **overrides: object) -> BusinessCalendar:
    return BusinessCalendar(time_zone=zone, **overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Business hours
# --------------------------------------------------------------------------


def test_within_one_day():
    assert add_business_minutes(london(2026, 9, 16, 10, 0), 90, calendar()) == london(
        2026, 9, 16, 11, 30
    )


def test_rolls_over_the_close_into_the_next_day():
    # 16:00 Wed + 2h: one hour Wednesday, one hour Thursday from 09:00.
    assert add_business_minutes(london(2026, 9, 16, 16, 0), 120, calendar()) == london(
        2026, 9, 17, 10, 0
    )


def test_rolls_over_the_weekend():
    # 16:00 Fri + 4h: one hour Friday, three hours Monday.
    assert add_business_minutes(london(2026, 9, 18, 16, 0), 240, calendar()) == london(
        2026, 9, 21, 12, 0
    )


def test_mail_arriving_out_of_hours_starts_the_clock_at_opening():
    assert add_business_minutes(london(2026, 9, 19, 3, 0), 60, calendar()) == london(
        2026, 9, 21, 10, 0
    )
    assert add_business_minutes(london(2026, 9, 16, 7, 0), 60, calendar()) == london(
        2026, 9, 16, 10, 0
    )
    assert add_business_minutes(london(2026, 9, 16, 17, 0), 60, calendar()) == london(
        2026, 9, 17, 10, 0
    )


def test_exactly_at_close_counts_as_closed():
    assert next_business_start(london(2026, 9, 16, 17, 0), calendar()) == london(2026, 9, 17, 9, 0)


def test_target_landing_exactly_on_close_stays_on_that_day():
    assert add_business_minutes(london(2026, 9, 16, 16, 0), 60, calendar()) == london(
        2026, 9, 16, 17, 0
    )


def test_holidays_are_skipped():
    cal = calendar(holidays=frozenset({date(2026, 9, 21)}))
    assert add_business_minutes(london(2026, 9, 18, 16, 0), 240, cal) == london(2026, 9, 22, 12, 0)


def test_non_standard_working_week():
    # A Sunday-to-Thursday week: Friday and Saturday are closed.
    cal = calendar("Asia/Dubai", business_days=(7, 1, 2, 3, 4))
    dubai = ZoneInfo("Asia/Dubai")
    start = datetime(2026, 9, 17, 16, 0, tzinfo=dubai)  # Thursday
    assert add_business_minutes(start, 120, cal) == datetime(
        2026, 9, 20, 10, 0, tzinfo=dubai
    )  # Sunday


def test_around_the_clock_policies_ignore_hours_and_holidays():
    cal = calendar(business_hours_only=False, holidays=frozenset({date(2026, 9, 19)}))
    assert add_business_minutes(london(2026, 9, 18, 23, 0), 120, cal) == london(2026, 9, 19, 1, 0)


def test_working_day_ending_at_midnight_loses_no_minutes():
    cal = BusinessCalendar("Asia/Kolkata", day_start_minute=0, day_end_minute=1440)
    friday_late = datetime(2026, 9, 18, 23, 0, tzinfo=KOLKATA)
    assert add_business_minutes(friday_late, 120, cal) == datetime(
        2026, 9, 21, 1, 0, tzinfo=KOLKATA
    )
    assert (
        business_minutes_between(
            datetime(2026, 9, 16, 0, 0, tzinfo=KOLKATA),
            datetime(2026, 9, 17, 0, 0, tzinfo=KOLKATA),
            cal,
        )
        == 1440
    )


# --- the server's zone is never the client's --------------------------------


def test_the_same_instant_gives_different_targets_in_different_zones():
    instant = datetime(2026, 9, 16, 15, 30, tzinfo=UTC)  # 16:30 London, 11:30 NY, 21:00 Kolkata
    two_hours = 120

    assert add_business_minutes(instant, two_hours, calendar("Europe/London")) == london(
        2026, 9, 17, 10, 30
    )
    assert add_business_minutes(instant, two_hours, calendar("America/New_York")) == datetime(
        2026, 9, 16, 13, 30, tzinfo=NEW_YORK
    )
    assert add_business_minutes(instant, two_hours, calendar("Asia/Kolkata")) == datetime(
        2026, 9, 17, 11, 0, tzinfo=KOLKATA
    )


def test_results_are_the_same_whatever_zone_the_input_is_expressed_in():
    as_utc = datetime(2026, 9, 16, 15, 30, tzinfo=UTC)
    as_tokyo = as_utc.astimezone(ZoneInfo("Asia/Tokyo"))
    assert add_business_minutes(as_utc, 120, calendar()) == add_business_minutes(
        as_tokyo, 120, calendar()
    )


def test_naive_timestamps_are_refused():
    with pytest.raises(BusinessHoursError, match="aware"):
        add_business_minutes(datetime(2026, 9, 16, 10, 0), 60, calendar())


def test_unknown_zones_are_refused():
    with pytest.raises(BusinessHoursError, match="not a known time zone"):
        calendar("Mars/Olympus_Mons")


@pytest.mark.parametrize(
    "overrides",
    [
        {"day_start_minute": 1020, "day_end_minute": 540},
        {"day_start_minute": 600, "day_end_minute": 600},
        {"day_end_minute": 1441},
        {"business_days": ()},
        {"business_days": (0, 1)},
        {"business_days": (8,)},
    ],
)
def test_unusable_calendars_are_refused(overrides):
    with pytest.raises(BusinessHoursError):
        calendar(**overrides)


def test_a_calendar_whose_only_days_are_holidays_fails_rather_than_loops():
    every_monday = frozenset(date(2026, 9, 21) + timedelta(weeks=week) for week in range(60))
    cal = calendar(business_days=(1,), holidays=every_monday)
    with pytest.raises(BusinessHoursError):
        add_business_minutes(london(2026, 9, 16, 10, 0), 60, cal)


def test_negative_targets_are_refused():
    with pytest.raises(BusinessHoursError):
        add_business_minutes(london(2026, 9, 16, 10, 0), -1, calendar())


# --- daylight saving -------------------------------------------------------


def test_opening_hour_is_local_on_both_sides_of_the_autumn_change():
    # UK clocks go back on Sunday 25 October 2026.
    before = add_business_minutes(london(2026, 10, 23, 16, 0), 240, calendar())
    assert before == london(2026, 10, 26, 12, 0)
    assert before.utcoffset() == timedelta(0)  # Monday is GMT
    assert london(2026, 10, 23, 16, 0).utcoffset() == timedelta(hours=1)  # Friday was BST


def test_opening_hour_is_local_on_both_sides_of_the_spring_change():
    # UK clocks go forward on Sunday 29 March 2026.
    after = add_business_minutes(london(2026, 3, 27, 16, 0), 240, calendar())
    assert after == london(2026, 3, 30, 12, 0)
    assert after.utcoffset() == timedelta(hours=1)


def test_a_day_that_spans_the_spring_gap_is_an_hour_shorter_in_real_time():
    # A 24x7 support desk working 00:00-06:00 on the day the clocks change.
    cal = calendar(day_start_minute=0, day_end_minute=360, business_days=(7,))
    sunday = london(2026, 3, 29, 0, 0)
    # 06:00 BST on that day is only five real hours after midnight GMT.
    assert business_minutes_between(sunday, london(2026, 3, 30, 0, 0), cal) == 300
    assert add_business_minutes(sunday, 300, cal) == london(2026, 3, 29, 6, 0)


def test_a_day_that_spans_the_autumn_repeat_is_an_hour_longer_in_real_time():
    cal = calendar(day_start_minute=0, day_end_minute=360, business_days=(7,))
    sunday = london(2026, 10, 25, 0, 0)
    assert business_minutes_between(sunday, london(2026, 10, 26, 0, 0), cal) == 420


def test_a_target_longer_than_the_shortened_spring_day_carries_over():
    # 330 minutes fits a naive six-hour day but not the real five-hour one:
    # 300 are used on 29 March, the last 30 on the next Sunday.
    cal = calendar(day_start_minute=0, day_end_minute=360, business_days=(7,))
    assert add_business_minutes(london(2026, 3, 29, 0, 0), 330, cal) == london(2026, 4, 5, 0, 30)


def test_a_target_that_fits_only_the_lengthened_autumn_day_stays_on_it():
    # 400 minutes does not fit a naive six-hour day but does fit the real
    # seven-hour one: 00:00 BST is 23:00 UTC, plus 6h40m is 05:40 GMT.
    cal = calendar(day_start_minute=0, day_end_minute=360, business_days=(7,))
    due = add_business_minutes(london(2026, 10, 25, 0, 0), 400, cal)
    assert due == datetime(2026, 10, 25, 5, 40, tzinfo=UTC)
    assert due.utcoffset() == timedelta(0)


def test_opening_inside_the_spring_gap_resolves_to_a_real_instant():
    # A desk opening at 01:30, which does not exist in London on 29 March.
    cal = calendar(day_start_minute=90, day_end_minute=600, business_days=(7,))
    opens = next_business_start(london(2026, 3, 29, 0, 0), cal)
    # The instant must survive a round trip, i.e. it genuinely occurred.
    assert opens.astimezone(UTC).astimezone(LONDON) == opens
    assert opens == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)  # 02:30 BST


def test_new_york_and_london_change_clocks_on_different_weekends():
    # Between 8 and 29 March 2026 New York is on summer time and London is not;
    # the gap between them is four hours, not five.
    instant = datetime(2026, 3, 16, 14, 0, tzinfo=UTC)  # 14:00 London, 10:00 NY
    assert add_business_minutes(instant, 60, calendar("Europe/London")) == london(
        2026, 3, 16, 15, 0
    )
    assert add_business_minutes(instant, 60, calendar("America/New_York")) == datetime(
        2026, 3, 16, 11, 0, tzinfo=NEW_YORK
    )


@pytest.mark.parametrize(
    "start",
    [
        london(2026, 9, 18, 16, 0),
        london(2026, 10, 23, 12, 15),
        london(2026, 3, 27, 9, 0),
        london(2026, 12, 24, 14, 45),
    ],
)
@pytest.mark.parametrize("minutes", [1, 59, 480, 481, 2400])
def test_elapsed_business_minutes_inverts_adding_them(start, minutes):
    cal = calendar(holidays=frozenset({date(2026, 12, 25), date(2026, 12, 28)}))
    due = add_business_minutes(start, minutes, cal)
    assert business_minutes_between(start, due, cal) == minutes


def test_elapsed_business_minutes_is_never_negative():
    assert (
        business_minutes_between(london(2026, 9, 17, 10, 0), london(2026, 9, 16, 10, 0), calendar())
        == 0
    )


# --------------------------------------------------------------------------
# Service-level policies
# --------------------------------------------------------------------------

DEFAULT = SlaPolicy("p-default", "Default", 480, time_zone="Europe/London")
CRITICAL = SlaPolicy("p-critical", "Critical", 60, urgency="critical", time_zone="Europe/London")
BILLING = SlaPolicy(
    "p-billing", "Billing department", 240, department_id="d-billing", time_zone="Europe/London"
)
BILLING_QUEUE = SlaPolicy(
    "p-bq", "Billing queue", 120, queue_id="q-billing", time_zone="Europe/London"
)
BILLING_CRITICAL = SlaPolicy(
    "p-bc",
    "Billing critical",
    30,
    department_id="d-billing",
    urgency="critical",
    time_zone="Europe/London",
)
POLICIES = [DEFAULT, CRITICAL, BILLING, BILLING_QUEUE, BILLING_CRITICAL]


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (TicketScope(), "Default"),
        (TicketScope(urgency="low"), "Default"),
        (TicketScope(urgency="critical"), "Critical"),
        (TicketScope(department_id="d-billing"), "Billing department"),
        # Department (2) outranks urgency (1)...
        (TicketScope(department_id="d-billing", urgency="high"), "Billing department"),
        # ...but department and urgency together (3) outrank either alone.
        (TicketScope(department_id="d-billing", urgency="critical"), "Billing critical"),
        # And a queue (4) outranks both together.
        (
            TicketScope(queue_id="q-billing", department_id="d-billing", urgency="critical"),
            "Billing queue",
        ),
        (TicketScope(department_id="d-support"), "Default"),
    ],
)
def test_the_most_specific_policy_wins(scope, expected):
    selected = select_policy(POLICIES, scope)
    assert selected is not None and selected.name == expected


def test_policy_selection_does_not_depend_on_row_order():
    scope = TicketScope(queue_id="q-billing", department_id="d-billing", urgency="critical")
    for rotation in range(len(POLICIES)):
        rotated = POLICIES[rotation:] + POLICIES[:rotation]
        assert select_policy(rotated, scope).name == "Billing queue"  # type: ignore[union-attr]
        assert select_policy(list(reversed(rotated)), scope).name == "Billing queue"  # type: ignore[union-attr]


def test_equally_specific_policies_are_a_conflict_not_a_coin_toss():
    twin = SlaPolicy(
        "p-twin", "Critical (duplicate)", 15, urgency="critical", time_zone="Europe/London"
    )
    with pytest.raises(SlaConflict) as raised:
        select_policy([*POLICIES, twin], TicketScope(urgency="critical"))
    assert raised.value.policy_names == ("Critical", "Critical (duplicate)")

    targets = compute_targets(
        [*POLICIES, twin], TicketScope(urgency="critical"), received_at=london(2026, 9, 16, 10, 0)
    )
    assert targets.reason == "SLA_POLICY_CONFLICT"
    assert not targets.has_target


def test_a_less_specific_twin_is_not_a_conflict():
    # Two defaults would conflict, but only if the conversation falls to them.
    twin_default = SlaPolicy("p-d2", "Default 2", 60, time_zone="Europe/London")
    selected = select_policy([*POLICIES, twin_default], TicketScope(urgency="critical"))
    assert selected is not None and selected.name == "Critical"


def test_no_policy_means_no_target_never_on_time():
    targets = compute_targets(
        [CRITICAL], TicketScope(urgency="low"), received_at=london(2026, 9, 16, 10, 0)
    )
    assert targets.reason == "NO_SLA_POLICY"
    assert evaluate_status(targets, now=london(2026, 9, 16, 10, 0)).state == "NOT_APPLICABLE"


def test_targets_use_the_policy_zone_not_the_input_zone():
    new_york_policy = SlaPolicy("p-ny", "NY", 60, time_zone="America/New_York")
    received = datetime(2026, 9, 16, 21, 0, tzinfo=UTC)  # 17:00 NY: closed
    targets = compute_targets([new_york_policy], TicketScope(), received_at=received)
    assert targets.first_response_due_at == datetime(2026, 9, 17, 10, 0, tzinfo=NEW_YORK)


def test_resolution_target_is_computed_on_the_same_calendar():
    policy = SlaPolicy("p", "P", 60, resolution_minutes=960, time_zone="Europe/London")
    targets = compute_targets([policy], TicketScope(), received_at=london(2026, 9, 18, 16, 0))
    # 16:00 Friday + 1h lands exactly on Friday's close.
    assert targets.first_response_due_at == london(2026, 9, 18, 17, 0)
    # 16h: 1h Friday, 8h Monday, 7h Tuesday.
    assert targets.resolution_due_at == london(2026, 9, 22, 16, 0)


def test_holidays_passed_to_targets_are_observed():
    targets = compute_targets(
        [DEFAULT],
        TicketScope(),
        received_at=london(2026, 12, 24, 16, 0),
        holidays=frozenset({date(2026, 12, 25), date(2026, 12, 28)}),
    )
    # 1h on the 24th, then the 25th (holiday), weekend, 28th (holiday): 7h on the 29th.
    assert targets.first_response_due_at == london(2026, 12, 29, 16, 0)


def test_an_unusable_policy_calendar_produces_no_target():
    broken = SlaPolicy("p", "Broken", 60, time_zone="Not/AZone")
    targets = compute_targets([broken], TicketScope(), received_at=london(2026, 9, 16, 10, 0))
    assert targets.reason == "SLA_CALENDAR_INVALID"
    assert not targets.has_target


# --- status ------------------------------------------------------------------

TARGETS = SlaTargets("p", "P", london(2026, 9, 16, 12, 0), None)


def test_status_on_track_then_at_risk_then_breached():
    assert evaluate_status(TARGETS, now=london(2026, 9, 16, 10, 0)).state == "ON_TRACK"
    edge = london(2026, 9, 16, 12, 0) - timedelta(minutes=AT_RISK_MINUTES)
    assert evaluate_status(TARGETS, now=edge).state == "AT_RISK"
    assert evaluate_status(TARGETS, now=edge - timedelta(minutes=1)).state == "ON_TRACK"
    assert evaluate_status(TARGETS, now=london(2026, 9, 16, 12, 0)).state == "BREACHED"


def test_a_reply_before_the_target_is_met_and_after_is_breached():
    assert (
        evaluate_status(
            TARGETS, now=london(2026, 9, 17, 0, 0), first_response_at=london(2026, 9, 16, 11, 59)
        ).state
        == "MET"
    )
    assert (
        evaluate_status(
            TARGETS, now=london(2026, 9, 16, 12, 5), first_response_at=london(2026, 9, 16, 12, 1)
        ).state
        == "BREACHED"
    )


def test_pausing_extends_the_target():
    status = evaluate_status(TARGETS, now=london(2026, 9, 16, 12, 30), paused_minutes=60)
    assert status.state == "AT_RISK"
    assert status.minutes_remaining == 30


def test_remaining_time_is_counted_in_working_minutes_over_a_weekend():
    friday_due = SlaTargets("p", "P", london(2026, 9, 21, 9, 20), None)
    # 17:00 Friday to 09:20 Monday is 64 wall-clock hours but 20 working minutes.
    status = evaluate_status(friday_due, now=london(2026, 9, 18, 17, 0), calendar=calendar())
    assert status.minutes_remaining == 20
    assert status.state == "AT_RISK"
