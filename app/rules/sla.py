"""Choosing a service-level policy and computing its targets.

Two decisions live here, and both are places a support product goes wrong
quietly rather than loudly:

* **Which policy applies.** A conversation can match several: one for the
  queue, one for the department, one for critical urgency. Picking "the first
  one found" means the answer depends on row order. Specificity decides, and
  ties are a conflict rather than a coin toss.
* **What the target time is.** Computed through the policy's own business
  calendar, in the policy's own zone (C-D044).

A conversation with no applicable policy gets no target. It is never assumed to
be on time - the workspace shows "No SLA target" for exactly this reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.rules.business_hours import (
    BusinessCalendar,
    BusinessHoursError,
    add_business_minutes,
    business_minutes_between,
)

# How long before a target counts as "at risk". Matches the workspace display.
AT_RISK_MINUTES = 30


class SlaConflict(Exception):
    """Two policies are equally specific. The client must decide, not the code."""

    def __init__(self, policy_names: tuple[str, ...]):
        super().__init__(f"Equally specific service-level policies: {', '.join(policy_names)}")
        self.policy_names = policy_names


@dataclass(frozen=True)
class SlaPolicy:
    id: str
    name: str
    first_response_minutes: int
    resolution_minutes: int | None = None
    queue_id: str | None = None
    department_id: str | None = None
    urgency: str | None = None
    time_zone: str = "Etc/UTC"
    business_hours_only: bool = True
    day_start_minute: int = 540
    day_end_minute: int = 1020
    business_days: tuple[int, ...] = (1, 2, 3, 4, 5)
    version: int = 1

    @property
    def specificity(self) -> int:
        """How narrowly this policy is targeted.

        Queue is the narrowest scope a client configures, then department, then
        urgency. A policy naming more of them wins over one naming fewer.
        """
        return (
            (4 if self.queue_id else 0)
            + (2 if self.department_id else 0)
            + (1 if self.urgency else 0)
        )

    def calendar(self, holidays: frozenset[date] = frozenset()) -> BusinessCalendar:
        return BusinessCalendar(
            time_zone=self.time_zone,
            day_start_minute=self.day_start_minute,
            day_end_minute=self.day_end_minute,
            business_days=self.business_days,
            holidays=holidays,
            business_hours_only=self.business_hours_only,
        )


@dataclass(frozen=True)
class TicketScope:
    queue_id: str | None = None
    department_id: str | None = None
    urgency: str | None = None


@dataclass(frozen=True)
class SlaTargets:
    policy_id: str | None
    policy_name: str | None
    first_response_due_at: datetime | None
    resolution_due_at: datetime | None
    reason: str | None = None

    @property
    def has_target(self) -> bool:
        return self.first_response_due_at is not None


def applicable_policies(policies: list[SlaPolicy], scope: TicketScope) -> list[SlaPolicy]:
    """Policies whose every named scope matches this conversation."""
    matching: list[SlaPolicy] = []
    for policy in policies:
        if policy.queue_id and policy.queue_id != scope.queue_id:
            continue
        if policy.department_id and policy.department_id != scope.department_id:
            continue
        if policy.urgency and policy.urgency != scope.urgency:
            continue
        matching.append(policy)
    return matching


def select_policy(policies: list[SlaPolicy], scope: TicketScope) -> SlaPolicy | None:
    """The single most specific policy, or none. Raises on a genuine tie."""
    matching = applicable_policies(policies, scope)
    if not matching:
        return None

    best = max(policy.specificity for policy in matching)
    finalists = [policy for policy in matching if policy.specificity == best]

    if len(finalists) > 1:
        # Two policies claiming the same conversation equally is a
        # configuration question, and guessing would make targets depend on
        # row order.
        raise SlaConflict(tuple(sorted(policy.name for policy in finalists)))
    return finalists[0]


def compute_targets(
    policies: list[SlaPolicy],
    scope: TicketScope,
    *,
    received_at: datetime,
    holidays: frozenset[date] = frozenset(),
) -> SlaTargets:
    """Targets for a conversation, or an explained absence of them."""
    try:
        policy = select_policy(policies, scope)
    except SlaConflict:
        return SlaTargets(None, None, None, None, reason="SLA_POLICY_CONFLICT")

    if policy is None:
        return SlaTargets(None, None, None, None, reason="NO_SLA_POLICY")

    try:
        calendar = policy.calendar(holidays)
        first_response = add_business_minutes(received_at, policy.first_response_minutes, calendar)
        resolution = (
            add_business_minutes(received_at, policy.resolution_minutes, calendar)
            if policy.resolution_minutes is not None
            else None
        )
    except BusinessHoursError:
        # An unusable calendar must not silently produce a target computed in
        # the wrong zone.
        return SlaTargets(policy.id, policy.name, None, None, reason="SLA_CALENDAR_INVALID")

    return SlaTargets(policy.id, policy.name, first_response, resolution)


@dataclass(frozen=True)
class SlaStatus:
    state: str
    minutes_remaining: int | None = None
    paused_minutes: int = 0


def evaluate_status(
    targets: SlaTargets,
    *,
    now: datetime,
    first_response_at: datetime | None = None,
    resolved: bool = False,
    paused_minutes: int = 0,
    calendar: BusinessCalendar | None = None,
) -> SlaStatus:
    """Where a conversation stands against its target."""
    if not targets.has_target:
        return SlaStatus("NOT_APPLICABLE")

    due = targets.first_response_due_at
    assert due is not None

    if first_response_at is not None:
        return SlaStatus("MET" if first_response_at <= due else "BREACHED")
    if resolved:
        return SlaStatus("MET")

    effective_due = due + timedelta(minutes=paused_minutes)
    if now >= effective_due:
        return SlaStatus("BREACHED", 0, paused_minutes)

    # Remaining time is measured in working minutes when the policy only
    # promises working hours: "two hours left" over a weekend is a lie.
    remaining = (
        business_minutes_between(now, effective_due, calendar)
        if calendar is not None
        else int((effective_due - now).total_seconds() // 60)
    )
    state = "AT_RISK" if remaining <= AT_RISK_MINUTES else "ON_TRACK"
    return SlaStatus(state, remaining, paused_minutes)
