"""What the gateway reads and writes, independent of where it is stored."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from app.gateway.policy import ModelApproval, ProviderConfig


@dataclass(frozen=True)
class CallOpening:
    organization_id: str
    correlation_id: str
    operation: str
    provider: str
    model: str
    region: str | None
    purpose: str
    fallback_from: str | None
    period_month: date
    reserve_micro: int
    budget_micro: int
    started_at: datetime


@dataclass(frozen=True)
class CallSettlement:
    outcome: str  # SUCCEEDED or FAILED
    failure_code: str | None
    http_status: int | None
    cost_micro: int
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int
    finished_at: datetime


@dataclass(frozen=True)
class UnbilledCall:
    """A call that holds no money: a refusal before calling, or a key check."""

    organization_id: str
    correlation_id: str
    operation: str
    provider: str
    model: str
    region: str | None
    purpose: str
    outcome: str  # REFUSED, SUCCEEDED, or FAILED
    failure_code: str | None
    at: datetime
    latency_ms: int | None = None
    http_status: int | None = None


@dataclass(frozen=True)
class PeriodSpend:
    spent_micro: int
    reserved_micro: int


class GatewayStore(Protocol):
    def load_configs(self, organization_id: str) -> list[ProviderConfig]: ...

    def load_approvals(self, organization_id: str) -> list[ModelApproval]: ...

    def open_call(self, opening: CallOpening) -> str | None:
        """Reserve budget and record a pending call atomically; None if over budget."""
        ...

    def settle_call(self, call_id: str, settlement: CallSettlement) -> None: ...

    def record_unbilled(self, call: UnbilledCall) -> None: ...

    def period_spend(
        self, organization_id: str, provider: str, period_month: date
    ) -> PeriodSpend: ...

    def mark_outcome(
        self, organization_id: str, provider: str, *, failure_code: str | None, at: datetime
    ) -> None: ...

    def mark_verification(
        self,
        organization_id: str,
        provider: str,
        *,
        failure_code: str | None,
        at: datetime,
        last_four: str | None = None,
    ) -> None: ...

    def clear_failure(self, organization_id: str, provider: str, codes: list[str]) -> None:
        """Clear the provider's failure code only if it is one of `codes`."""
        ...

    def release_stale_reservations(self, before: datetime, *, at: datetime) -> int: ...

    def recent_failures(self, organization_id: str, since: datetime) -> dict[str, int]: ...

    def recent_fallbacks(self, organization_id: str, since: datetime) -> int:
        """Successful calls in the window that ran on a fallback model."""
        ...

    def record_audit(
        self,
        *,
        organization_id: str,
        actor_membership_id: str | None,
        action: str,
        outcome: str,
        reason_code: str | None,
        target_id: str,
        metadata: dict[str, object],
    ) -> None: ...
