"""PostgreSQL gateway store.

The budget check and the reservation are one statement. A read-then-write
check lets two concurrent calls both see headroom and both spend it; the
conditional UPDATE below cannot, because PostgreSQL re-evaluates its WHERE
clause against the row version it locks.

Settlement moves a reservation into spend exactly once: the call row's
`reservation_state` is flipped from HELD in the same statement that reads the
amount, so a retried settlement finds nothing to move.

Every method runs in its own short transaction, taken from the engine. A model
call can take a minute; holding a transaction across it would keep the ledger
row locked - serialising every AI call for the provider - and keep a database
connection checked out for the whole wait. Reserve and commit, call, then
settle and commit.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from app.gateway.contract import Operation
from app.gateway.policy import ModelApproval, ProviderConfig
from app.gateway.records import CallOpening, CallSettlement, PeriodSpend, UnbilledCall

_USAGE_UPSERT = (
    "INSERT INTO provider_usage (organization_id, usage_date, provider, model, "
    "operation, request_count, failed_request_count, prompt_tokens, "
    "completion_tokens, estimated_cost_micro_units, estimated_cost_minor_units) "
    "VALUES (CAST(:org AS uuid), :day, :provider, :model, :operation, :ok, :failed, "
    ":prompt, :completion, :cost, :cost / 1000000) "
    "ON CONFLICT (organization_id, usage_date, provider, model, operation) DO UPDATE SET "
    "request_count = provider_usage.request_count + EXCLUDED.request_count, "
    "failed_request_count = provider_usage.failed_request_count "
    "+ EXCLUDED.failed_request_count, "
    "prompt_tokens = provider_usage.prompt_tokens + EXCLUDED.prompt_tokens, "
    "completion_tokens = provider_usage.completion_tokens + EXCLUDED.completion_tokens, "
    "estimated_cost_micro_units = provider_usage.estimated_cost_micro_units "
    "+ EXCLUDED.estimated_cost_micro_units, "
    "estimated_cost_minor_units = (provider_usage.estimated_cost_micro_units "
    "+ EXCLUDED.estimated_cost_micro_units) / 1000000, "
    "updated_at = now()"
)


class PostgresGatewayStore:
    def __init__(self, engine: Any):
        self._engine = engine

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        with self._engine.begin() as connection:
            yield connection

    def _rows(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        with self._tx() as c:
            return [dict(row._mapping) for row in c.execute(text(sql), params)]

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def load_configs(self, organization_id: str) -> list[ProviderConfig]:
        rows = self._rows(
            "SELECT organization_id::text AS organization_id, provider, credential_secret_name, "
            "credential_last_four, classification_model, generation_model, embedding_model, "
            "embedding_dimensions, processing_region, monthly_budget_minor_units, "
            "budget_currency, fallback_provider, fallback_classification_model, "
            "fallback_generation_model, is_active, last_verified_at, last_failure_code, "
            "last_failure_at FROM ai_configs "
            "WHERE organization_id = CAST(:org AS uuid) AND is_active ORDER BY provider",
            org=organization_id,
        )
        return [
            ProviderConfig(
                organization_id=row["organization_id"],
                provider=row["provider"],
                credential_secret_name=row["credential_secret_name"],
                credential_last_four=row["credential_last_four"],
                classification_model=row["classification_model"],
                generation_model=row["generation_model"],
                embedding_model=row["embedding_model"],
                embedding_dimensions=int(row["embedding_dimensions"]),
                region=row["processing_region"],
                monthly_budget_minor_units=row["monthly_budget_minor_units"],
                currency=row["budget_currency"],
                fallback_provider=row["fallback_provider"],
                fallback_classification_model=row["fallback_classification_model"],
                fallback_generation_model=row["fallback_generation_model"],
                is_active=row["is_active"],
                last_verified_at=row["last_verified_at"],
                last_failure_code=row["last_failure_code"],
                last_failure_at=row["last_failure_at"],
            )
            for row in rows
        ]

    def load_approvals(self, organization_id: str) -> list[ModelApproval]:
        rows = self._rows(
            "SELECT provider, model, operation, region, input_price_per_million_minor, "
            "output_price_per_million_minor, max_output_tokens FROM ai_model_approvals "
            "WHERE organization_id = CAST(:org AS uuid) AND revoked_at IS NULL",
            org=organization_id,
        )
        return [
            ModelApproval(
                provider=row["provider"],
                model=row["model"],
                operation=Operation(row["operation"]),
                region=row["region"],
                input_price_per_million_minor=int(row["input_price_per_million_minor"]),
                output_price_per_million_minor=int(row["output_price_per_million_minor"]),
                max_output_tokens=row["max_output_tokens"],
            )
            for row in rows
        ]

    def period_spend(self, organization_id: str, provider: str, period_month: date) -> PeriodSpend:
        rows = self._rows(
            "SELECT spent_micro_units, reserved_micro_units FROM provider_budget_ledgers "
            "WHERE organization_id = CAST(:org AS uuid) AND provider = :provider "
            "AND period_month = :period",
            org=organization_id,
            provider=provider,
            period=period_month,
        )
        if not rows:
            return PeriodSpend(0, 0)
        return PeriodSpend(int(rows[0]["spent_micro_units"]), int(rows[0]["reserved_micro_units"]))

    def recent_failures(self, organization_id: str, since: datetime) -> dict[str, int]:
        rows = self._rows(
            "SELECT failure_code, count(*) AS n FROM provider_calls "
            "WHERE organization_id = CAST(:org AS uuid) AND started_at >= :since "
            "AND failure_code IS NOT NULL GROUP BY failure_code ORDER BY failure_code",
            org=organization_id,
            since=since,
        )
        return {row["failure_code"]: int(row["n"]) for row in rows}

    def recent_fallbacks(self, organization_id: str, since: datetime) -> int:
        rows = self._rows(
            "SELECT count(*) AS n FROM provider_calls "
            "WHERE organization_id = CAST(:org AS uuid) AND started_at >= :since "
            "AND outcome = 'SUCCEEDED' AND fallback_from IS NOT NULL",
            org=organization_id,
            since=since,
        )
        return int(rows[0]["n"])

    # ------------------------------------------------------------------
    # Money
    # ------------------------------------------------------------------

    def open_call(self, opening: CallOpening) -> str | None:
        params: dict[str, Any] = {
            "org": opening.organization_id,
            "provider": opening.provider,
            "period": opening.period_month,
            "amount": opening.reserve_micro,
            "budget": opening.budget_micro,
        }
        with self._tx() as c:
            c.execute(
                text(
                    "INSERT INTO provider_budget_ledgers (organization_id, provider, "
                    "period_month) VALUES (CAST(:org AS uuid), :provider, :period) "
                    "ON CONFLICT DO NOTHING"
                ),
                params,
            )
            reserved = c.execute(
                text(
                    "UPDATE provider_budget_ledgers SET reserved_micro_units = "
                    "reserved_micro_units + :amount, updated_at = now() "
                    "WHERE organization_id = CAST(:org AS uuid) AND provider = :provider "
                    "AND period_month = :period "
                    "AND spent_micro_units + reserved_micro_units + :amount <= :budget "
                    "RETURNING id"
                ),
                params,
            ).first()
            if reserved is None:
                return None
            call_id = c.execute(
                text(
                    "INSERT INTO provider_calls (organization_id, correlation_id, operation, "
                    "provider, model, region, purpose, outcome, fallback_from, period_month, "
                    "reserved_micro_units, reservation_state, started_at) VALUES "
                    "(CAST(:org AS uuid), :correlation, :operation, :provider, :model, :region, "
                    ":purpose, 'PENDING', :fallback_from, :period, :amount, 'HELD', :started) "
                    "RETURNING id::text"
                ),
                {
                    **params,
                    "correlation": opening.correlation_id,
                    "operation": opening.operation,
                    "model": opening.model,
                    "region": opening.region,
                    "purpose": opening.purpose,
                    "fallback_from": opening.fallback_from,
                    "started": opening.started_at,
                },
            ).scalar_one()
        return str(call_id)

    def settle_call(self, call_id: str, settlement: CallSettlement) -> None:
        with self._tx() as c:
            row = c.execute(
                text(
                    "UPDATE provider_calls SET outcome = :outcome, failure_code = :code, "
                    "http_status = :status, cost_micro_units = :cost, prompt_tokens = :prompt, "
                    "completion_tokens = :completion, latency_ms = :latency, "
                    "finished_at = :finished, reservation_state = 'SETTLED' "
                    "WHERE id = CAST(:id AS uuid) AND reservation_state = 'HELD' "
                    "RETURNING organization_id::text AS org, provider, model, operation, "
                    "period_month, reserved_micro_units"
                ),
                {
                    "id": call_id,
                    "outcome": settlement.outcome,
                    "code": settlement.failure_code,
                    "status": settlement.http_status,
                    "cost": settlement.cost_micro,
                    "prompt": settlement.prompt_tokens,
                    "completion": settlement.completion_tokens,
                    "latency": settlement.latency_ms,
                    "finished": settlement.finished_at,
                },
            ).first()
            if row is None:
                return  # Already settled or released: nothing to move twice.

            c.execute(
                text(
                    "UPDATE provider_budget_ledgers SET "
                    "reserved_micro_units = reserved_micro_units - :reserved, "
                    "spent_micro_units = spent_micro_units + :cost, updated_at = now() "
                    "WHERE organization_id = CAST(:org AS uuid) AND provider = :provider "
                    "AND period_month = :period"
                ),
                {
                    "org": row.org,
                    "provider": row.provider,
                    "period": row.period_month,
                    "reserved": row.reserved_micro_units,
                    "cost": settlement.cost_micro,
                },
            )
            succeeded = settlement.outcome == "SUCCEEDED"
            c.execute(
                text(_USAGE_UPSERT),
                {
                    "org": row.org,
                    "day": settlement.finished_at.date(),
                    "provider": row.provider,
                    "model": row.model,
                    "operation": row.operation,
                    "ok": 1 if succeeded else 0,
                    "failed": 0 if succeeded else 1,
                    "prompt": settlement.prompt_tokens or 0,
                    "completion": settlement.completion_tokens or 0,
                    "cost": settlement.cost_micro,
                },
            )

    def release_stale_reservations(self, before: datetime, *, at: datetime) -> int:
        """Return money held by calls whose worker died mid-flight."""
        with self._tx() as c:
            released = c.execute(
                text(
                    "UPDATE provider_calls SET outcome = 'FAILED', "
                    "failure_code = 'PROVIDER_TIMEOUT', reservation_state = 'RELEASED', "
                    "finished_at = :at, cost_micro_units = reserved_micro_units "
                    "WHERE reservation_state = 'HELD' AND started_at < :before "
                    "RETURNING organization_id, provider, period_month, reserved_micro_units"
                ),
                {"before": before, "at": at},
            ).all()
            for row in released:
                # The provider may have processed the request before the worker
                # died, so the reservation is charged, not refunded.
                c.execute(
                    text(
                        "UPDATE provider_budget_ledgers SET "
                        "reserved_micro_units = reserved_micro_units - :amount, "
                        "spent_micro_units = spent_micro_units + :amount, updated_at = now() "
                        "WHERE organization_id = :org AND provider = :provider "
                        "AND period_month = :period"
                    ),
                    {
                        "org": row.organization_id,
                        "provider": row.provider,
                        "period": row.period_month,
                        "amount": row.reserved_micro_units,
                    },
                )
        return len(released)

    # ------------------------------------------------------------------
    # Status and history
    # ------------------------------------------------------------------

    def record_unbilled(self, call: UnbilledCall) -> None:
        with self._tx() as c:
            c.execute(
                text(
                    "INSERT INTO provider_calls (organization_id, correlation_id, operation, "
                    "provider, model, region, purpose, outcome, failure_code, http_status, "
                    "latency_ms, started_at, finished_at) VALUES (CAST(:org AS uuid), "
                    ":correlation, :operation, :provider, :model, :region, :purpose, :outcome, "
                    ":code, :status, :latency, :at, :at)"
                ),
                {
                    "org": call.organization_id,
                    "correlation": call.correlation_id,
                    "operation": call.operation,
                    "provider": call.provider,
                    "model": call.model,
                    "region": call.region,
                    "purpose": call.purpose,
                    "outcome": call.outcome,
                    "code": call.failure_code,
                    "status": call.http_status,
                    "latency": call.latency_ms,
                    "at": call.at,
                },
            )

    def mark_outcome(
        self, organization_id: str, provider: str, *, failure_code: str | None, at: datetime
    ) -> None:
        if failure_code is None:
            sql = (
                "UPDATE ai_configs SET last_success_at = :at, last_failure_code = NULL, "
                "updated_at = now() WHERE organization_id = CAST(:org AS uuid) "
                "AND provider = :provider AND is_active"
            )
        else:
            sql = (
                "UPDATE ai_configs SET last_failure_code = :code, last_failure_at = :at, "
                "updated_at = now() WHERE organization_id = CAST(:org AS uuid) "
                "AND provider = :provider AND is_active"
            )
        with self._tx() as c:
            c.execute(
                text(sql),
                {"org": organization_id, "provider": provider, "code": failure_code, "at": at},
            )

    def clear_failure(self, organization_id: str, provider: str, codes: list[str]) -> None:
        with self._tx() as c:
            c.execute(
                text(
                    "UPDATE ai_configs SET last_failure_code = NULL, updated_at = now() "
                    "WHERE organization_id = CAST(:org AS uuid) AND provider = :provider "
                    "AND is_active AND last_failure_code = ANY(:codes)"
                ),
                {"org": organization_id, "provider": provider, "codes": codes},
            )

    def mark_verification(
        self,
        organization_id: str,
        provider: str,
        *,
        failure_code: str | None,
        at: datetime,
        last_four: str | None = None,
    ) -> None:
        with self._tx() as c:
            c.execute(
                text(
                    "UPDATE ai_configs SET "
                    "last_verified_at = CASE WHEN CAST(:code AS text) IS NULL "
                    "THEN CAST(:at AS timestamptz) ELSE last_verified_at END, "
                    "last_verification_error = :code, "
                    "credential_last_four = COALESCE(:last_four, credential_last_four), "
                    "updated_at = now() "
                    "WHERE organization_id = CAST(:org AS uuid) AND provider = :provider "
                    "AND is_active"
                ),
                {
                    "org": organization_id,
                    "provider": provider,
                    "code": failure_code,
                    "at": at,
                    "last_four": last_four,
                },
            )

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
    ) -> None:
        with self._tx() as c:
            c.execute(
                text(
                    "INSERT INTO audit_events (organization_id, actor_membership_id, action, "
                    "outcome, reason_code, target_type, target_id, metadata) VALUES "
                    "(CAST(:org AS uuid), CAST(:membership AS uuid), :action, :outcome, "
                    ":reason, 'ai_provider', :target, CAST(:metadata AS jsonb))"
                ),
                {
                    "org": organization_id,
                    "membership": actor_membership_id,
                    "action": action,
                    "outcome": outcome,
                    "reason": reason_code,
                    "target": target_id,
                    "metadata": json.dumps(metadata, sort_keys=True),
                },
            )
