"""In-memory gateway store for offline tests.

Mirrors the PostgreSQL store's guarantees - reservation checked and taken in
one step, settlement moving money exactly once - so the service's tests prove
the same behaviour the live database is later checked for.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from app.gateway.policy import ModelApproval, ProviderConfig
from app.gateway.records import CallOpening, CallSettlement, PeriodSpend, UnbilledCall


@dataclass
class CallRow:
    id: str
    organization_id: str
    correlation_id: str
    operation: str
    provider: str
    model: str
    region: str | None
    purpose: str
    outcome: str
    failure_code: str | None = None
    http_status: int | None = None
    fallback_from: str | None = None
    period_month: date | None = None
    reserved_micro: int = 0
    reservation_state: str = "NONE"
    cost_micro: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class InMemoryGatewayStore:
    configs: list[ProviderConfig] = field(default_factory=list)
    approvals: list[ModelApproval] = field(default_factory=list)
    calls: list[CallRow] = field(default_factory=list)
    ledgers: dict[tuple[str, str, date], list[int]] = field(default_factory=dict)
    usage: dict[tuple[str, date, str, str, str], dict[str, int]] = field(default_factory=dict)
    audits: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def load_configs(self, organization_id: str) -> list[ProviderConfig]:
        return [c for c in self.configs if c.organization_id == organization_id and c.is_active]

    def load_approvals(self, organization_id: str) -> list[ModelApproval]:
        # Approvals in tests are scoped by the store instance.
        return list(self.approvals)

    def open_call(self, opening: CallOpening) -> str | None:
        with self._lock:
            key = (opening.organization_id, opening.provider, opening.period_month)
            ledger = self.ledgers.setdefault(key, [0, 0])  # spent, reserved
            if ledger[0] + ledger[1] + opening.reserve_micro > opening.budget_micro:
                return None
            ledger[1] += opening.reserve_micro
            row = CallRow(
                id=str(uuid.uuid4()),
                organization_id=opening.organization_id,
                correlation_id=opening.correlation_id,
                operation=opening.operation,
                provider=opening.provider,
                model=opening.model,
                region=opening.region,
                purpose=opening.purpose,
                outcome="PENDING",
                fallback_from=opening.fallback_from,
                period_month=opening.period_month,
                reserved_micro=opening.reserve_micro,
                reservation_state="HELD",
                started_at=opening.started_at,
            )
            self.calls.append(row)
            return row.id

    def settle_call(self, call_id: str, settlement: CallSettlement) -> None:
        with self._lock:
            row = next((c for c in self.calls if c.id == call_id), None)
            if row is None or row.reservation_state != "HELD":
                return
            row.outcome = settlement.outcome
            row.failure_code = settlement.failure_code
            row.http_status = settlement.http_status
            row.cost_micro = settlement.cost_micro
            row.prompt_tokens = settlement.prompt_tokens
            row.completion_tokens = settlement.completion_tokens
            row.latency_ms = settlement.latency_ms
            row.finished_at = settlement.finished_at
            row.reservation_state = "SETTLED"
            assert row.period_month is not None
            ledger = self.ledgers[(row.organization_id, row.provider, row.period_month)]
            ledger[1] -= row.reserved_micro
            ledger[0] += settlement.cost_micro
            assert ledger[1] >= 0, "reservation released twice"
            key = (
                row.organization_id,
                settlement.finished_at.date(),
                row.provider,
                row.model,
                row.operation,
            )
            usage = self.usage.setdefault(
                key, {"requests": 0, "failed": 0, "prompt": 0, "completion": 0, "cost_micro": 0}
            )
            usage["requests" if settlement.outcome == "SUCCEEDED" else "failed"] += 1
            usage["prompt"] += settlement.prompt_tokens or 0
            usage["completion"] += settlement.completion_tokens or 0
            usage["cost_micro"] += settlement.cost_micro

    def record_unbilled(self, call: UnbilledCall) -> None:
        self.calls.append(
            CallRow(
                id=str(uuid.uuid4()),
                organization_id=call.organization_id,
                correlation_id=call.correlation_id,
                operation=call.operation,
                provider=call.provider,
                model=call.model,
                region=call.region,
                purpose=call.purpose,
                outcome=call.outcome,
                failure_code=call.failure_code,
                http_status=call.http_status,
                latency_ms=call.latency_ms,
                started_at=call.at,
                finished_at=call.at,
            )
        )

    def period_spend(self, organization_id: str, provider: str, period_month: date) -> PeriodSpend:
        spent, reserved = self.ledgers.get((organization_id, provider, period_month), [0, 0])
        return PeriodSpend(spent, reserved)

    def _update_config(self, organization_id: str, provider: str, **changes: Any) -> None:
        self.configs = [
            replace(c, **changes)
            if c.organization_id == organization_id and c.provider == provider and c.is_active
            else c
            for c in self.configs
        ]

    def mark_outcome(
        self, organization_id: str, provider: str, *, failure_code: str | None, at: datetime
    ) -> None:
        if failure_code is None:
            self._update_config(organization_id, provider, last_failure_code=None)
        else:
            self._update_config(
                organization_id, provider, last_failure_code=failure_code, last_failure_at=at
            )

    def clear_failure(self, organization_id: str, provider: str, codes: list[str]) -> None:
        self.configs = [
            replace(c, last_failure_code=None)
            if c.organization_id == organization_id
            and c.provider == provider
            and c.is_active
            and c.last_failure_code in codes
            else c
            for c in self.configs
        ]

    def mark_verification(
        self,
        organization_id: str,
        provider: str,
        *,
        failure_code: str | None,
        at: datetime,
        last_four: str | None = None,
    ) -> None:
        changes: dict[str, Any] = {}
        if failure_code is None:
            changes["last_verified_at"] = at
        if last_four is not None:
            changes["credential_last_four"] = last_four
        self.verification_errors[(organization_id, provider)] = failure_code
        self._update_config(organization_id, provider, **changes)

    @property
    def verification_errors(self) -> dict[tuple[str, str], str | None]:
        if not hasattr(self, "_verification_errors"):
            self._verification_errors: dict[tuple[str, str], str | None] = {}
        return self._verification_errors

    def release_stale_reservations(self, before: datetime, *, at: datetime) -> int:
        released = 0
        with self._lock:
            for row in self.calls:
                if (
                    row.reservation_state == "HELD"
                    and row.started_at is not None
                    and row.started_at < before
                ):
                    assert row.period_month is not None
                    ledger = self.ledgers[(row.organization_id, row.provider, row.period_month)]
                    ledger[1] -= row.reserved_micro
                    ledger[0] += row.reserved_micro
                    row.outcome = "FAILED"
                    row.failure_code = "PROVIDER_TIMEOUT"
                    row.reservation_state = "RELEASED"
                    row.cost_micro = row.reserved_micro
                    row.finished_at = at
                    released += 1
        return released

    def recent_failures(self, organization_id: str, since: datetime) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.calls:
            if (
                row.organization_id == organization_id
                and row.failure_code
                and row.started_at is not None
                and row.started_at >= since
            ):
                counts[row.failure_code] = counts.get(row.failure_code, 0) + 1
        return dict(sorted(counts.items()))

    def recent_fallbacks(self, organization_id: str, since: datetime) -> int:
        return sum(
            1
            for row in self.calls
            if row.organization_id == organization_id
            and row.outcome == "SUCCEEDED"
            and row.fallback_from
            and row.started_at is not None
            and row.started_at >= since
        )

    def record_audit(self, **event: Any) -> None:
        self.audits.append(event)
