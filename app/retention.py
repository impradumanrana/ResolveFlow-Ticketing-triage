"""Retention sweeps: what gets deleted, when, and what stops it.

Deleting client data is irreversible, so this module is built so that the
decision and the deletion are separate things:

* `build_sweep_plan` is pure. Given policies and a clock it returns the cutoff
  for each data class and nothing else. Every boundary case - a disabled
  policy, an absent policy, an out-of-range retention - is decided here and is
  testable with no database.
* `execute_sweep` performs the deletion. It refuses to invent a policy, honours
  legal holds, defaults to a dry run, and records every sweep in an append-only
  table. A deletion with no record of the deletion is indistinguishable from
  data loss.

Retention is deliberately *not* expressed as scattered `DELETE ... WHERE age >
n` statements. A legal hold must be able to stop deletion, and an auditor must
be able to ask what the policy was on a given day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# Only these classes may be swept, and only through the column named here.
# Adding a class means adding a deliberate entry, not a new call site.
RETENTION_TARGETS: dict[str, tuple[str, str]] = {
    "MESSAGE": ("messages", "sent_at"),
    "ATTACHMENT": ("attachments", "created_at"),
    "TRIAGE_RUN": ("triage_runs", "created_at"),
    "JOB": ("jobs", "created_at"),
    "USAGE": ("provider_usage", "created_at"),
    "EVALUATION": ("evaluation_runs", "started_at"),
}

# Deliberately absent from RETENTION_TARGETS:
#   AUDIT_EVENT - append-only by database trigger; removing audit history is a
#                 separate, explicitly approved procedure, not a routine sweep.
#   TICKET      - deleting a ticket orphans its conversation and its decision
#                 history. Ticket deletion is a privacy-request workflow (C13),
#                 not an age-based sweep.
UNSWEEPABLE_CLASSES = frozenset({"AUDIT_EVENT", "TICKET"})

MIN_RETAIN_DAYS = 1
MAX_RETAIN_DAYS = 3650


class RetentionError(Exception):
    """A refusal to sweep. Always fatal for that data class."""


@dataclass(frozen=True)
class RetentionPolicy:
    data_class: str
    retain_days: int
    enabled: bool = True


@dataclass(frozen=True)
class SweepTarget:
    data_class: str
    table: str
    timestamp_column: str
    cutoff_at: datetime
    retain_days: int


@dataclass(frozen=True)
class SweepPlan:
    organization_id: str
    targets: tuple[SweepTarget, ...]
    skipped: tuple[tuple[str, str], ...]  # (data_class, reason)

    def describe(self) -> str:
        lines = [f"Retention sweep plan for organization {self.organization_id}"]
        for target in self.targets:
            lines.append(
                f"  {target.data_class:<12} delete {target.table} "
                f"older than {target.cutoff_at.isoformat()} "
                f"({target.retain_days} days)"
            )
        for data_class, reason in self.skipped:
            lines.append(f"  {data_class:<12} skipped: {reason}")
        return "\n".join(lines)


def build_sweep_plan(
    organization_id: str,
    policies: list[RetentionPolicy],
    now: datetime | None = None,
) -> SweepPlan:
    """Decide what would be deleted. Pure: no database, no clock of its own."""

    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise RetentionError("Retention must be computed against an aware timestamp.")

    targets: list[SweepTarget] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()

    for policy in policies:
        if policy.data_class in seen:
            raise RetentionError(
                f"Duplicate retention policy for {policy.data_class}; "
                "the effective policy would be ambiguous."
            )
        seen.add(policy.data_class)

        if policy.data_class in UNSWEEPABLE_CLASSES:
            skipped.append(
                (policy.data_class, "class is never swept by age; see C13 workflows")
            )
            continue

        target = RETENTION_TARGETS.get(policy.data_class)
        if target is None:
            skipped.append((policy.data_class, "no sweep target is defined"))
            continue

        if not policy.enabled:
            skipped.append((policy.data_class, "policy is disabled"))
            continue

        if not MIN_RETAIN_DAYS <= policy.retain_days <= MAX_RETAIN_DAYS:
            raise RetentionError(
                f"Retention for {policy.data_class} is {policy.retain_days} days, "
                f"outside the permitted {MIN_RETAIN_DAYS}-{MAX_RETAIN_DAYS}."
            )

        table, column = target
        targets.append(
            SweepTarget(
                data_class=policy.data_class,
                table=table,
                timestamp_column=column,
                cutoff_at=moment - timedelta(days=policy.retain_days),
                retain_days=policy.retain_days,
            )
        )

    # A class with no policy is never swept. Absence of a policy is not
    # permission to delete.
    for data_class in RETENTION_TARGETS:
        if data_class not in seen:
            skipped.append((data_class, "no policy recorded"))

    return SweepPlan(
        organization_id=organization_id,
        targets=tuple(targets),
        skipped=tuple(skipped),
    )


def load_policies(connection: Any, organization_id: str) -> list[RetentionPolicy]:
    from sqlalchemy import text

    rows = connection.execute(
        text(
            "SELECT data_class::text, retain_days, enabled "
            "FROM retention_policies WHERE organization_id = :organization_id "
            "ORDER BY data_class"
        ),
        {"organization_id": organization_id},
    ).all()
    return [
        RetentionPolicy(data_class=row[0], retain_days=row[1], enabled=row[2])
        for row in rows
    ]


def active_holds(connection: Any, organization_id: str) -> set[str]:
    """Data classes currently under a legal hold.

    A hold with a null `data_class` is organization-wide and stops every sweep.
    """
    from sqlalchemy import text

    rows = connection.execute(
        text(
            "SELECT data_class::text FROM legal_holds "
            "WHERE organization_id = :organization_id AND released_at IS NULL"
        ),
        {"organization_id": organization_id},
    ).all()

    held: set[str] = set()
    for row in rows:
        if row[0] is None:
            return set(RETENTION_TARGETS) | {"__ALL__"}
        held.add(row[0])
    return held


def execute_sweep(
    connection: Any,
    plan: SweepPlan,
    *,
    dry_run: bool = True,
) -> dict[str, int]:
    """Apply the plan. Defaults to a dry run; deletion must be asked for."""

    from sqlalchemy import text

    held = active_holds(connection, plan.organization_id)
    results: dict[str, int] = {}

    for target in plan.targets:
        if target.data_class in held or "__ALL__" in held:
            held_count = connection.execute(
                text(
                    f"SELECT count(*) FROM {target.table} "  # noqa: S608 - table from a fixed map
                    f"WHERE organization_id = :organization_id "
                    f"AND {target.timestamp_column} < :cutoff"
                ),
                {"organization_id": plan.organization_id, "cutoff": target.cutoff_at},
            ).scalar_one()

            _record_sweep(connection, plan, target, deleted=0, held=held_count, dry_run=dry_run)
            results[target.data_class] = 0
            continue

        if dry_run:
            would_delete = connection.execute(
                text(
                    f"SELECT count(*) FROM {target.table} "  # noqa: S608
                    f"WHERE organization_id = :organization_id "
                    f"AND {target.timestamp_column} < :cutoff"
                ),
                {"organization_id": plan.organization_id, "cutoff": target.cutoff_at},
            ).scalar_one()
            _record_sweep(
                connection, plan, target, deleted=would_delete, held=0, dry_run=True
            )
            results[target.data_class] = would_delete
            continue

        deleted = connection.execute(
            text(
                f"DELETE FROM {target.table} "  # noqa: S608
                f"WHERE organization_id = :organization_id "
                f"AND {target.timestamp_column} < :cutoff"
            ),
            {"organization_id": plan.organization_id, "cutoff": target.cutoff_at},
        ).rowcount
        _record_sweep(connection, plan, target, deleted=deleted, held=0, dry_run=False)
        results[target.data_class] = deleted

    return results


def _record_sweep(
    connection: Any,
    plan: SweepPlan,
    target: SweepTarget,
    *,
    deleted: int,
    held: int,
    dry_run: bool,
) -> None:
    from sqlalchemy import text

    connection.execute(
        text(
            "INSERT INTO retention_sweeps "
            "(organization_id, data_class, cutoff_at, rows_deleted, rows_held, dry_run) "
            "VALUES (:organization_id, CAST(:data_class AS retention_data_class), "
            ":cutoff, :deleted, :held, :dry_run)"
        ),
        {
            "organization_id": plan.organization_id,
            "data_class": target.data_class,
            "cutoff": target.cutoff_at,
            "deleted": deleted,
            "held": held,
            "dry_run": dry_run,
        },
    )
