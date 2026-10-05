"""Business-hours arithmetic in the client's own time zone.

Adding "four working hours" to a timestamp is where service-level targets
quietly go wrong. The traps, all of which this module is built around:

* **The server's zone is never the client's.** A target computed in UTC is
  wrong by the offset for every client outside it, and wrong differently in
  summer (C-D044).
* **Daylight saving moves the clock, not the working day.** A day is not 24
  hours twice a year. Business hours are anchored to local wall-clock times, so
  09:00 is 09:00 in both March and November.
* **Spring-forward deletes wall-clock times.** In Europe/London, 01:00 to 02:00
  does not exist on the last Sunday in March. Naive arithmetic produces a
  timestamp that never occurs.
* **Autumn fall-back repeats them.** 01:30 happens twice; one of those is an
  hour later than the other in real elapsed time.

Everything here works in local wall-clock terms and converts once, at the
boundary, using `fold` to resolve the repeated hour deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MINUTES_IN_DAY = 24 * 60
# A target that would take longer than this to satisfy is a configuration
# error, not a service level. Bounded so a bad policy cannot loop.
MAX_SEARCH_DAYS = 400


class BusinessHoursError(ValueError):
    """A calendar that cannot be used to compute a target."""


@dataclass(frozen=True)
class BusinessCalendar:
    """When work happens, expressed in one named zone."""

    time_zone: str
    # Minutes from local midnight. 540 is 09:00, 1020 is 17:00.
    day_start_minute: int = 540
    day_end_minute: int = 1020
    # ISO weekday numbers: Monday is 1, Sunday is 7.
    business_days: tuple[int, ...] = (1, 2, 3, 4, 5)
    holidays: frozenset[date] = field(default_factory=frozenset)
    # When false, the clock runs continuously and holidays are ignored.
    business_hours_only: bool = True

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise BusinessHoursError(
                f"{self.time_zone!r} is not a known time zone. Service-level "
                "targets must be computed in a real zone, never the server's."
            ) from error

        if not 0 <= self.day_start_minute < self.day_end_minute <= MINUTES_IN_DAY:
            raise BusinessHoursError("Business hours must be a non-empty range inside one day.")
        if self.business_hours_only and not self.business_days:
            raise BusinessHoursError(
                "A business-hours calendar with no business days can never meet a target."
            )
        if any(day < 1 or day > 7 for day in self.business_days):
            raise BusinessHoursError("Business days are ISO weekdays, 1 (Monday) to 7 (Sunday).")

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)

    @property
    def minutes_per_day(self) -> int:
        return self.day_end_minute - self.day_start_minute

    def is_business_day(self, day: date) -> bool:
        if not self.business_hours_only:
            return True
        return day.isoweekday() in self.business_days and day not in self.holidays


def _local(moment: datetime, calendar: BusinessCalendar) -> datetime:
    if moment.tzinfo is None:
        raise BusinessHoursError(
            "Service-level arithmetic requires an aware timestamp; a naive one "
            "would be interpreted in whatever zone the server happens to use."
        )
    return moment.astimezone(calendar.zone)


def _at_local_minute(day: date, minute: int, calendar: BusinessCalendar) -> datetime:
    """Build a local timestamp, stepping over a time the clock skipped.

    On a spring-forward day the requested wall-clock time may not exist. Python
    invents an offset rather than failing, so the result is normalised through
    UTC and nudged forward until it is a time that genuinely occurred.
    """
    if minute >= MINUTES_IN_DAY:
        # "Until midnight" is the start of the next day, not 23:59 - clamping
        # would quietly drop a minute from every working day.
        return _at_local_minute(day + timedelta(days=1), 0, calendar)

    hour, minute_of_hour = divmod(minute, 60)
    candidate = datetime.combine(day, time(hour, minute_of_hour), tzinfo=calendar.zone)

    # A skipped local time round-trips to a different wall clock.
    normalised = candidate.astimezone(UTC).astimezone(calendar.zone)
    while normalised.date() == day and (normalised.hour, normalised.minute) < (
        hour,
        minute_of_hour,
    ):
        candidate += timedelta(minutes=15)
        normalised = candidate.astimezone(UTC).astimezone(calendar.zone)
    return normalised


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


# Arithmetic note. Python subtracts and compares two datetimes that share a
# tzinfo by wall clock, ignoring their offsets: 06:00 minus 00:00 on the day the
# clocks go back is "6 hours" although 7 elapsed. Every difference, sum, and
# comparison below is therefore taken in UTC. Local time is used only to find
# which day it is and where that day's opening and closing fall.


def next_business_start(moment: datetime, calendar: BusinessCalendar) -> datetime:
    """The first working instant at or after `moment`."""
    local = _local(moment, calendar)
    if not calendar.business_hours_only:
        return local

    instant = _utc(local)
    for offset in range(MAX_SEARCH_DAYS):
        day = local.date() + timedelta(days=offset)
        if not calendar.is_business_day(day):
            continue
        opens = _at_local_minute(day, calendar.day_start_minute, calendar)
        closes = _at_local_minute(day, calendar.day_end_minute, calendar)
        if offset == 0 and instant >= _utc(closes):
            continue
        if offset == 0 and instant > _utc(opens):
            return local
        return opens

    raise BusinessHoursError(
        "No business day found within the search window; the calendar excludes every day."
    )


def add_business_minutes(start: datetime, minutes: int, calendar: BusinessCalendar) -> datetime:
    """Add working minutes to `start`, skipping closed hours, weekends, and holidays."""
    if minutes < 0:
        raise BusinessHoursError("Service-level targets cannot be negative.")

    if not calendar.business_hours_only:
        return (_utc(start) + timedelta(minutes=minutes)).astimezone(calendar.zone)

    remaining = minutes
    cursor = next_business_start(start, calendar)

    for _ in range(MAX_SEARCH_DAYS):
        closes = _at_local_minute(cursor.date(), calendar.day_end_minute, calendar)

        # Real elapsed minutes, so a day shortened or lengthened by a clock
        # change is counted as the time it actually was.
        available = int((_utc(closes) - _utc(cursor)).total_seconds() // 60)
        if remaining <= available:
            return (_utc(cursor) + timedelta(minutes=remaining)).astimezone(calendar.zone)

        remaining -= max(available, 0)
        cursor = next_business_start(closes, calendar)

    raise BusinessHoursError("Service-level target could not be reached within the search window.")


def business_minutes_between(start: datetime, end: datetime, calendar: BusinessCalendar) -> int:
    """Working minutes elapsed between two instants. Never negative."""
    begin, finish = _utc(_local(start, calendar)), _utc(_local(end, calendar))
    if finish <= begin:
        return 0
    if not calendar.business_hours_only:
        return int((finish - begin).total_seconds() // 60)

    total = 0
    day = _local(start, calendar).date()
    last_day = _local(end, calendar).date()

    for _ in range(MAX_SEARCH_DAYS + 1):
        if day > last_day:
            return total
        if calendar.is_business_day(day):
            opens = _utc(_at_local_minute(day, calendar.day_start_minute, calendar))
            closes = _utc(_at_local_minute(day, calendar.day_end_minute, calendar))
            window_start = max(begin, opens)
            window_end = min(finish, closes)
            if window_end > window_start:
                total += int((window_end - window_start).total_seconds() // 60)
        day += timedelta(days=1)

    raise BusinessHoursError("Elapsed time spans more than the search window.")
