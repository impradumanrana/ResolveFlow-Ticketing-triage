"""The gateway: the only way the client deployment reaches a model."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel

from app.gateway.contract import (
    Completion,
    CompletionRequest,
    Embeddings,
    Operation,
    ProviderAdapter,
)
from app.gateway.credentials import CredentialCache, SecretValue, last_four, mask, validate_new_key
from app.gateway.errors import (
    CLEARED_BY_VERIFICATION,
    AttemptRecord,
    FailureCode,
    GatewayFailure,
    failure_message,
)
from app.gateway.policy import (
    MICRO,
    Candidate,
    ProviderConfig,
    budget_period,
    cost_micro,
    estimate_tokens,
    find_approval,
    plan_route,
)
from app.gateway.records import CallOpening, CallSettlement, GatewayStore, UnbilledCall
from app.mailbox.access import Actor
from app.security.untrusted import UNTRUSTED_NOTICE, wrap_untrusted

T = TypeVar("T")

# Mirrors the TypeScript role matrix for ai_settings.view / ai_settings.manage.
VIEW_ROLES = frozenset({"OWNER", "ADMIN", "SUPERVISOR", "AUDITOR"})
MANAGE_ROLES = frozenset({"OWNER", "ADMIN"})

MAX_ATTEMPTS_PER_MODEL = 2
BASE_BACKOFF_SECONDS = 1.0
# A provider asking us to wait longer than this is better served by the
# fallback, or by a person, than by holding a worker.
MAX_RETRY_WAIT_SECONDS = 10.0
STRUCTURE_REPAIRS = 1
MAX_REPAIR_INPUT_CHARACTERS = 20_000
STALE_RESERVATION = timedelta(minutes=15)
FAILURE_WINDOW = timedelta(hours=24)

TIMEOUT_SECONDS = {
    Operation.CLASSIFICATION: 30.0,
    Operation.GENERATION: 60.0,
    Operation.EMBEDDING: 30.0,
}
DEFAULT_MAX_OUTPUT_TOKENS = {
    Operation.CLASSIFICATION: 400,
    Operation.GENERATION: 1500,
}
FALLBACK_USED = "PROVIDER_FALLBACK_USED"

REPAIR_INSTRUCTION = (
    "Repair the supplied value into one valid JSON object that satisfies the required "
    f"structure. Return only that JSON object. {UNTRUSTED_NOTICE}"
)


class AccessDenied(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GatewayResult:
    data: dict[str, Any]
    provider: str
    model: str
    attempts: tuple[AttemptRecord, ...]
    prompt_tokens: int
    completion_tokens: int
    cost_micro: int

    @property
    def fallback_used(self) -> bool:
        return any(a.outcome == "SUCCEEDED" and a.fallback_from for a in self.attempts)

    @property
    def rule_codes(self) -> tuple[str, ...]:
        return (FALLBACK_USED,) if self.fallback_used else ()


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str
    model: str
    prompt_tokens: int
    cost_micro: int


@dataclass(frozen=True)
class VerificationReport:
    provider: str
    ok: bool
    code: str | None
    checked_models: tuple[str, ...]
    stored: bool = False


@dataclass(frozen=True)
class ProviderView:
    """Everything an operator may see about a provider. No key, no secret name."""

    provider: str
    status: str
    credential_hint: str
    region: str | None
    models: dict[str, str]
    fallback: tuple[str, ...]
    budget_minor_units: int | None
    currency: str
    spent_minor_units: float
    reserved_minor_units: float
    last_verified_at: datetime | None
    last_failure_code: str | None
    last_failure_message: str | None
    last_failure_at: datetime | None
    approved_models: tuple[str, ...]


@dataclass(frozen=True)
class FailureSummary:
    code: str
    count: int
    message: str | None


@dataclass(frozen=True)
class SettingsView:
    providers: tuple[ProviderView, ...]
    recent_failures: tuple[FailureSummary, ...] = ()
    fallback_uses: int = 0


@dataclass
class _CallTotals:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_micro: int = 0


@dataclass
class _Key:
    secret_name: str
    value: SecretValue
    refreshed: bool = False


class AIGateway:
    def __init__(
        self,
        store: GatewayStore,
        credentials: CredentialCache,
        adapters: dict[str, ProviderAdapter],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        index_dimensions: int | None = None,
    ):
        self.store = store
        self.credentials = credentials
        self.adapters = adapters
        self.now = now
        self.sleep = sleep
        self.monotonic = monotonic
        if index_dimensions is None:
            from app.knowledge.embeddings import index_dimensions as configured

            index_dimensions = configured()
        self.index_dimensions = index_dimensions

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------

    def complete(
        self,
        organization_id: str,
        operation: Operation,
        *,
        system: str,
        user: str,
        correlation_id: str,
        schema: type[BaseModel] | None = None,
        max_output_tokens: int | None = None,
    ) -> GatewayResult:
        if operation is Operation.EMBEDDING:
            raise ValueError("Use embed() for embeddings.")
        candidates, attempts = self._route(organization_id, operation, correlation_id)
        limit = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS[operation]

        failures: list[GatewayFailure] = []
        for candidate in candidates:
            totals = _CallTotals()
            data: dict[str, Any] | None = None
            failure: GatewayFailure | None = None
            try:
                data = self._complete_with(
                    candidate, operation, system, user, schema, limit, correlation_id, totals
                )
            except GatewayFailure as caught:
                failure = caught
            if failure is not None:
                attempts.append(self._attempt(candidate, "FAILED", failure.code))
                self.store.mark_outcome(
                    organization_id,
                    candidate.config.provider,
                    failure_code=failure.code.value,
                    at=self.now(),
                )
                failures.append(failure)
                if not failure.policy.try_fallback:
                    raise self._final(failure, failure.code, attempts)
                continue
            assert data is not None

            attempts.append(self._attempt(candidate, "SUCCEEDED", None))
            self.store.mark_outcome(
                organization_id, candidate.config.provider, failure_code=None, at=self.now()
            )
            return GatewayResult(
                data=data,
                provider=candidate.config.provider,
                model=candidate.model,
                attempts=tuple(attempts),
                prompt_tokens=totals.prompt_tokens,
                completion_tokens=totals.completion_tokens,
                cost_micro=totals.cost_micro,
            )

        last = failures[-1]
        code = FailureCode.ALL_CANDIDATES_FAILED if len(failures) > 1 else last.code
        raise self._final(last, code, attempts)

    def embed(
        self, organization_id: str, texts: list[str], *, correlation_id: str
    ) -> EmbeddingResult:
        candidates, _ = self._route(organization_id, Operation.EMBEDDING, correlation_id)
        candidate = candidates[0]  # plan_route never offers an embedding fallback
        config = candidate.config
        if not texts:
            return EmbeddingResult([], config.provider, candidate.model, 0, 0)

        if config.embedding_dimensions != self.index_dimensions:
            self._refuse(
                candidate,
                Operation.EMBEDDING,
                correlation_id,
                FailureCode.EMBEDDING_DIMENSIONS_MISMATCH,
            )
            raise GatewayFailure(
                FailureCode.EMBEDDING_DIMENSIONS_MISMATCH,
                provider=config.provider,
                model=candidate.model,
            )

        prompt = "".join(texts)
        adapter = self._adapter(candidate, Operation.EMBEDDING, correlation_id)
        budget = self._budget(candidate, Operation.EMBEDDING, correlation_id)
        key = self._key(candidate, Operation.EMBEDDING, correlation_id)
        totals = _CallTotals()
        region = str(config.region)

        def invoke(credential: SecretValue) -> Embeddings:
            return adapter.embed(
                credential,
                model=candidate.model,
                region=region,
                inputs=list(texts),
                dimensions=self.index_dimensions,
                timeout=TIMEOUT_SECONDS[Operation.EMBEDDING],
            )

        def interpret(raw: Embeddings) -> list[list[float]]:
            if len(raw.vectors) != len(texts) or any(
                len(vector) != self.index_dimensions for vector in raw.vectors
            ):
                raise ValueError("embedding shape")
            return raw.vectors

        vectors: list[list[float]] = []
        code: FailureCode | None = None
        try:
            vectors = self._with_retries(
                candidate,
                key,
                Operation.EMBEDDING,
                "CALL",
                correlation_id,
                budget,
                prompt,
                0,
                invoke,
                interpret,
                totals,
            )
        except GatewayFailure as caught:
            code = caught.code
        except _InvalidOutput:
            # A wrong-shaped vector is never repaired: there is nothing to ask.
            code = FailureCode.INVALID_SCHEMA
        if code is not None:
            self.store.mark_outcome(
                organization_id, config.provider, failure_code=code.value, at=self.now()
            )
            raise GatewayFailure(code, provider=config.provider, model=candidate.model)
        self.store.mark_outcome(organization_id, config.provider, failure_code=None, at=self.now())
        return EmbeddingResult(
            vectors, config.provider, candidate.model, totals.prompt_tokens, totals.cost_micro
        )

    def release_stale_reservations(self) -> int:
        at = self.now()
        return self.store.release_stale_reservations(at - STALE_RESERVATION, at=at)

    # ------------------------------------------------------------------
    # Settings: view, verify, replace key
    # ------------------------------------------------------------------

    def describe(self, actor: Actor, organization_id: str) -> SettingsView:
        self._authorize(actor, organization_id, VIEW_ROLES, "ai_settings.view")
        configs = self.store.load_configs(organization_id)
        approvals = self.store.load_approvals(organization_id)
        period = budget_period(self.now())
        views = []
        for config in configs:
            spend = self.store.period_spend(organization_id, config.provider, period)
            views.append(
                ProviderView(
                    provider=config.provider,
                    status=_status(config),
                    credential_hint=(
                        f"••••{config.credential_last_four}"
                        if config.credential_last_four
                        else "not verified"
                    ),
                    region=config.region,
                    models={
                        operation.value: config.model_for(operation) for operation in Operation
                    },
                    fallback=tuple(
                        label
                        for label in (
                            config.fallback_classification_model
                            and f"classification: {config.fallback_classification_model}",
                            config.fallback_generation_model
                            and f"generation: {config.fallback_generation_model}",
                            config.fallback_provider and f"provider: {config.fallback_provider}",
                        )
                        if label
                    ),
                    budget_minor_units=config.monthly_budget_minor_units,
                    currency=config.currency,
                    spent_minor_units=spend.spent_micro / MICRO,
                    reserved_minor_units=spend.reserved_micro / MICRO,
                    last_verified_at=config.last_verified_at,
                    last_failure_code=config.last_failure_code,
                    last_failure_message=failure_message(config.last_failure_code),
                    last_failure_at=config.last_failure_at,
                    approved_models=tuple(
                        sorted(
                            f"{a.operation.value}: {a.model} ({a.region})"
                            for a in approvals
                            if a.provider == config.provider
                        )
                    ),
                )
            )
        since = self.now() - FAILURE_WINDOW
        return SettingsView(
            providers=tuple(views),
            recent_failures=tuple(
                FailureSummary(code, count, failure_message(code))
                for code, count in sorted(
                    self.store.recent_failures(organization_id, since).items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ),
            fallback_uses=self.store.recent_fallbacks(organization_id, since),
        )

    def verify(self, actor: Actor, organization_id: str, provider: str) -> VerificationReport:
        self._authorize(actor, organization_id, MANAGE_ROLES, "ai_settings.manage")
        config = self._config(organization_id, provider)
        try:
            key = self.credentials.get(config.credential_secret_name)
        except GatewayFailure as failure:
            report = VerificationReport(provider, False, failure.code.value, ())
        else:
            report = self._check_key(config, key, organization_id)
        self.store.mark_verification(
            organization_id, provider, failure_code=report.code, at=self.now()
        )
        if report.ok:
            self._clear_verified_failures(organization_id, provider)
        self._audit(actor, "ai_provider.verify", report, {"models": list(report.checked_models)})
        return report

    def replace_credential(
        self, actor: Actor, organization_id: str, provider: str, api_key: str
    ) -> VerificationReport:
        """Check a new key works, then store it. A key that fails is never stored."""
        self._authorize(actor, organization_id, MANAGE_ROLES, "ai_settings.manage")
        config = self._config(organization_id, provider)
        try:
            candidate = SecretValue(validate_new_key(api_key))
        except GatewayFailure as failure:
            report = VerificationReport(provider, False, failure.code.value, ())
            self._audit(actor, "ai_provider.credential.replace", report, {})
            return report

        report = self._check_key(config, candidate, organization_id)
        if not report.ok:
            # The working key stays in place; a typo must not cause an outage.
            self._audit(actor, "ai_provider.credential.replace", report, {})
            return report

        try:
            self.credentials.source.add_version(config.credential_secret_name, candidate)
        except GatewayFailure as failure:
            report = VerificationReport(provider, False, failure.code.value, report.checked_models)
            self._audit(actor, "ai_provider.credential.replace", report, {})
            return report

        self.credentials.invalidate(config.credential_secret_name)
        self.store.mark_verification(
            organization_id,
            provider,
            failure_code=None,
            at=self.now(),
            last_four=last_four(candidate.reveal()),
        )
        self._clear_verified_failures(organization_id, provider)
        stored = VerificationReport(provider, True, None, report.checked_models, stored=True)
        self._audit(actor, "ai_provider.credential.replace", stored, {"hint": candidate.hint})
        return stored

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _route(
        self, organization_id: str, operation: Operation, correlation_id: str
    ) -> tuple[tuple[Candidate, ...], list[AttemptRecord]]:
        configs = self.store.load_configs(organization_id)
        route = plan_route(configs, self.store.load_approvals(organization_id), operation)
        if route.failure is not None:
            first = configs[0] if configs else None
            self.store.record_unbilled(
                UnbilledCall(
                    organization_id=organization_id,
                    correlation_id=correlation_id,
                    operation=operation.value,
                    provider=first.provider if first else "none",
                    model=first.model_for(operation) if first else "none",
                    region=first.region if first else None,
                    purpose="CALL",
                    outcome="REFUSED",
                    failure_code=route.failure.value,
                    at=self.now(),
                )
            )
            if first is not None:
                self.store.mark_outcome(
                    organization_id,
                    first.provider,
                    failure_code=route.failure.value,
                    at=self.now(),
                )
            raise GatewayFailure(route.failure, provider=first.provider if first else None)

        attempts = [
            AttemptRecord(
                provider=label.split("/")[0],
                model=label.split("/")[1] if "/" in label else "",
                outcome="REJECTED",
                failure_code=code.value,
                fallback_from=route.candidates[0].label,
            )
            for label, code in route.rejected
        ]
        return route.candidates, attempts

    def _complete_with(
        self,
        candidate: Candidate,
        operation: Operation,
        system: str,
        user: str,
        schema: type[BaseModel] | None,
        limit: int,
        correlation_id: str,
        totals: _CallTotals,
    ) -> dict[str, Any]:
        adapter = self._adapter(candidate, operation, correlation_id)
        budget = self._budget(candidate, operation, correlation_id)
        key = self._key(candidate, operation, correlation_id)
        if candidate.approval.max_output_tokens:
            limit = min(limit, candidate.approval.max_output_tokens)
        region = str(candidate.config.region)

        request = CompletionRequest(system=system, user=user, max_output_tokens=limit)
        purpose = "CALL"
        for round_number in range(STRUCTURE_REPAIRS + 1):

            def invoke(credential: SecretValue, request: CompletionRequest = request) -> Completion:
                return adapter.complete(
                    credential,
                    model=candidate.model,
                    region=region,
                    request=request,
                    timeout=TIMEOUT_SECONDS[operation],
                )

            def interpret(raw: Completion) -> dict[str, Any]:
                if raw.finish_reason == "length":
                    raise _Truncated()
                if raw.finish_reason == "content_filter":
                    raise _Filtered()
                return _parse(raw.text, schema)

            invalid_text: str | None = None
            try:
                return self._with_retries(
                    candidate,
                    key,
                    operation,
                    purpose,
                    correlation_id,
                    budget,
                    request.system + request.user,
                    limit,
                    invoke,
                    interpret,
                    totals,
                )
            except _InvalidOutput as invalid:
                invalid_text = invalid.text
            if round_number == STRUCTURE_REPAIRS:
                raise GatewayFailure(
                    FailureCode.INVALID_SCHEMA,
                    provider=candidate.config.provider,
                    model=candidate.model,
                )
            request = CompletionRequest(
                system=f"{REPAIR_INSTRUCTION}\nRequired structure: {_describe(schema)}",
                # The value being repaired is the model's own malformed output,
                # which may be echoing text an attacker emailed in.
                user=wrap_untrusted("provider_output", invalid_text[:MAX_REPAIR_INPUT_CHARACTERS]),
                max_output_tokens=limit,
            )
            purpose = "REPAIR"
        raise AssertionError("unreachable")

    def _with_retries(
        self,
        candidate: Candidate,
        key: _Key,
        operation: Operation,
        purpose: str,
        correlation_id: str,
        budget_micro: int,
        prompt_text: str,
        output_cap: int,
        invoke: Callable[[SecretValue], Any],
        interpret: Callable[[Any], T],
        totals: _CallTotals,
    ) -> T:
        attempt = 1
        while True:
            try:
                return self._call(
                    candidate,
                    key,
                    operation,
                    purpose,
                    correlation_id,
                    budget_micro,
                    prompt_text,
                    output_cap,
                    invoke,
                    interpret,
                    totals,
                )
            except GatewayFailure as failure:
                if failure.code is FailureCode.CREDENTIAL_INVALID:
                    self.credentials.invalidate(key.secret_name)
                    if not key.refreshed:
                        # The cached key may be one the client has since
                        # rotated. One re-read, and one more try if it changed.
                        key.refreshed = True
                        fresh = self.credentials.get(key.secret_name)
                        if fresh != key.value:
                            key.value = fresh
                            continue
                        # Unchanged: the re-read cached the rejected key again.
                        self.credentials.invalidate(key.secret_name)
                    raise
                if not failure.policy.retry_same_model or attempt >= MAX_ATTEMPTS_PER_MODEL:
                    raise
                wait = (
                    failure.retry_after_seconds
                    if failure.retry_after_seconds is not None
                    else BASE_BACKOFF_SECONDS * 2 ** (attempt - 1)
                )
                if wait > MAX_RETRY_WAIT_SECONDS:
                    raise
                self.sleep(wait)
                attempt += 1

    def _call(
        self,
        candidate: Candidate,
        key: _Key,
        operation: Operation,
        purpose: str,
        correlation_id: str,
        budget_micro: int,
        prompt_text: str,
        output_cap: int,
        invoke: Callable[[SecretValue], Any],
        interpret: Callable[[Any], T],
        totals: _CallTotals,
    ) -> T:
        config = candidate.config
        prompt_estimate = estimate_tokens(prompt_text)
        reserve = cost_micro(candidate.approval, prompt_estimate, output_cap)
        started_at = self.now()
        call_id = self.store.open_call(
            CallOpening(
                organization_id=config.organization_id,
                correlation_id=correlation_id,
                operation=operation.value,
                provider=config.provider,
                model=candidate.model,
                region=config.region,
                purpose=purpose,
                fallback_from=candidate.fallback_from,
                period_month=budget_period(started_at),
                reserve_micro=reserve,
                budget_micro=budget_micro,
                started_at=started_at,
            )
        )
        if call_id is None:
            self._refuse(candidate, operation, correlation_id, FailureCode.BUDGET_EXCEEDED, purpose)
            raise GatewayFailure(
                FailureCode.BUDGET_EXCEEDED, provider=config.provider, model=candidate.model
            )

        clock = self.monotonic()

        def settle(
            outcome: str,
            code: FailureCode | None,
            status: int | None,
            cost: int,
            prompt: int | None,
            completion: int | None,
        ) -> None:
            self.store.settle_call(
                call_id,
                CallSettlement(
                    outcome=outcome,
                    failure_code=code.value if code else None,
                    http_status=status,
                    cost_micro=cost,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    latency_ms=max(0, round((self.monotonic() - clock) * 1000)),
                    finished_at=self.now(),
                ),
            )
            totals.cost_micro += cost
            totals.prompt_tokens += prompt or 0
            totals.completion_tokens += completion or 0

        # Every failure below is raised after its try block, never inside the
        # except clause: raising there chains the caught exception as
        # __context__, and a provider or validation error can carry key
        # fragments or customer content.
        raw: Any = None
        failure: GatewayFailure | None = None
        try:
            raw = invoke(key.value)
        except GatewayFailure as caught:
            failure = caught
        except Exception:
            failure = GatewayFailure(
                FailureCode.PROVIDER_UNAVAILABLE, provider=config.provider, model=candidate.model
            )
        if failure is not None:
            # Providers do not bill refused requests, but a request that timed
            # out - or failed in an unknown way - may have been processed.
            unknown = failure.code in (FailureCode.PROVIDER_TIMEOUT,)
            crashed = failure.status is None and failure.code is FailureCode.PROVIDER_UNAVAILABLE
            charged = reserve if unknown or crashed else 0
            settle("FAILED", failure.code, failure.status, charged, None, None)
            raise GatewayFailure(
                failure.code,
                provider=failure.provider or config.provider,
                model=failure.model or candidate.model,
                status=failure.status,
                retry_after_seconds=failure.retry_after_seconds,
            )

        prompt_tokens = getattr(raw, "prompt_tokens", None)
        completion_tokens = getattr(raw, "completion_tokens", None)
        # Missing usage is charged at the reservation, never as free.
        billed_prompt = prompt_tokens if prompt_tokens is not None else prompt_estimate
        billed_completion = completion_tokens if completion_tokens is not None else output_cap
        cost = cost_micro(candidate.approval, billed_prompt, billed_completion)

        data: Any = None
        output_code: FailureCode | None = None
        try:
            data = interpret(raw)
        except _Truncated:
            output_code = FailureCode.OUTPUT_TRUNCATED
        except _Filtered:
            output_code = FailureCode.CONTENT_FILTERED
        except (ValueError, TypeError):
            output_code = FailureCode.INVALID_SCHEMA

        if output_code is None:
            settle("SUCCEEDED", None, None, cost, billed_prompt, billed_completion)
            return data  # type: ignore[no-any-return]

        # A bad answer was still produced, so it is still paid for.
        settle("FAILED", output_code, None, cost, billed_prompt, billed_completion)
        if output_code is FailureCode.INVALID_SCHEMA:
            raise _InvalidOutput(getattr(raw, "text", ""))
        raise GatewayFailure(output_code, provider=config.provider, model=candidate.model)

    def _adapter(
        self, candidate: Candidate, operation: Operation, correlation_id: str
    ) -> ProviderAdapter:
        adapter = self.adapters.get(candidate.config.provider)
        if adapter is None:
            self._refuse(candidate, operation, correlation_id, FailureCode.PROVIDER_NOT_SUPPORTED)
            raise GatewayFailure(
                FailureCode.PROVIDER_NOT_SUPPORTED, provider=candidate.config.provider
            )
        if candidate.config.region not in adapter.supported_regions:
            self._refuse(candidate, operation, correlation_id, FailureCode.REGION_NOT_SUPPORTED)
            raise GatewayFailure(
                FailureCode.REGION_NOT_SUPPORTED, provider=candidate.config.provider
            )
        return adapter

    def _budget(self, candidate: Candidate, operation: Operation, correlation_id: str) -> int:
        budget = candidate.config.monthly_budget_minor_units
        if budget is None:
            self._refuse(candidate, operation, correlation_id, FailureCode.BUDGET_NOT_CONFIGURED)
            raise GatewayFailure(
                FailureCode.BUDGET_NOT_CONFIGURED, provider=candidate.config.provider
            )
        return int(budget) * MICRO

    def _key(self, candidate: Candidate, operation: Operation, correlation_id: str) -> _Key:
        name = candidate.config.credential_secret_name
        failure: GatewayFailure | None = None
        try:
            return _Key(name, self.credentials.get(name))
        except GatewayFailure as caught:
            failure = caught
        self._refuse(candidate, operation, correlation_id, failure.code)
        raise GatewayFailure(
            failure.code, provider=candidate.config.provider, status=failure.status
        )

    def _refuse(
        self,
        candidate: Candidate,
        operation: Operation,
        correlation_id: str,
        code: FailureCode,
        purpose: str = "CALL",
    ) -> None:
        self.store.record_unbilled(
            UnbilledCall(
                organization_id=candidate.config.organization_id,
                correlation_id=correlation_id,
                operation=operation.value,
                provider=candidate.config.provider,
                model=candidate.model,
                region=candidate.config.region,
                purpose=purpose,
                outcome="REFUSED",
                failure_code=code.value,
                at=self.now(),
            )
        )

    def _check_key(
        self, config: ProviderConfig, key: SecretValue, organization_id: str
    ) -> VerificationReport:
        """Verify every configured model with this key, without generating anything."""
        adapter = self.adapters.get(config.provider)
        if adapter is None:
            return VerificationReport(
                config.provider, False, FailureCode.PROVIDER_NOT_SUPPORTED.value, ()
            )
        if not config.region or config.region not in adapter.supported_regions:
            return VerificationReport(
                config.provider, False, FailureCode.REGION_NOT_SUPPORTED.value, ()
            )

        approvals = self.store.load_approvals(organization_id)
        wanted: list[tuple[Operation, str]] = []
        for operation in Operation:
            for model in (config.model_for(operation), config.fallback_model_for(operation)):
                if model and (operation, model) not in wanted:
                    wanted.append((operation, model))

        checked: list[str] = []
        for operation, model in wanted:
            if find_approval(approvals, config.provider, model, operation, config.region) is None:
                return VerificationReport(
                    config.provider, False, FailureCode.MODEL_NOT_APPROVED.value, tuple(checked)
                )
        for model in dict.fromkeys(model for _, model in wanted):
            clock = self.monotonic()
            code: FailureCode | None = None
            status: int | None = None
            try:
                adapter.verify(key, model=model, region=config.region, timeout=10.0)
            except GatewayFailure as failure:
                code, status = failure.code, failure.status
            except Exception:
                code = FailureCode.PROVIDER_UNAVAILABLE
            self.store.record_unbilled(
                UnbilledCall(
                    organization_id=organization_id,
                    correlation_id=f"verify-{config.provider}",
                    operation=next(op.value for op, m in wanted if m == model),
                    provider=config.provider,
                    model=model,
                    region=config.region,
                    purpose="VERIFY",
                    outcome="FAILED" if code else "SUCCEEDED",
                    failure_code=code.value if code else None,
                    at=self.now(),
                    latency_ms=max(0, round((self.monotonic() - clock) * 1000)),
                    http_status=status,
                )
            )
            if code is not None:
                return VerificationReport(config.provider, False, code.value, tuple(checked))
            checked.append(model)
        return VerificationReport(config.provider, True, None, tuple(checked))

    def _clear_verified_failures(self, organization_id: str, provider: str) -> None:
        self.store.clear_failure(
            organization_id, provider, sorted(code.value for code in CLEARED_BY_VERIFICATION)
        )

    def _config(self, organization_id: str, provider: str) -> ProviderConfig:
        for config in self.store.load_configs(organization_id):
            if config.provider == provider:
                return config
        raise GatewayFailure(FailureCode.CONFIG_MISSING, provider=provider)

    def _authorize(
        self, actor: Actor, organization_id: str, roles: frozenset[str], permission: str
    ) -> None:
        code = None
        if actor.organization_id != organization_id:
            code = "CROSS_ORGANIZATION"
        elif actor.status != "ACTIVE":
            code = "MEMBERSHIP_NOT_ACTIVE"
        elif actor.role not in roles:
            code = "ROLE_LACKS_PERMISSION"
        if code is None:
            return
        self.store.record_audit(
            organization_id=actor.organization_id,
            actor_membership_id=actor.membership_id,
            action=permission,
            outcome="DENIED",
            reason_code=code,
            target_id=organization_id,
            metadata={},
        )
        raise AccessDenied(code)

    def _audit(
        self, actor: Actor, action: str, report: VerificationReport, extra: dict[str, object]
    ) -> None:
        self.store.record_audit(
            organization_id=actor.organization_id,
            actor_membership_id=actor.membership_id,
            action=action,
            outcome="ALLOWED" if report.ok else "FAILED",
            reason_code=report.code,
            target_id=report.provider,
            metadata={"provider": report.provider, "stored": report.stored, **extra},
        )

    @staticmethod
    def _attempt(candidate: Candidate, outcome: str, code: FailureCode | None) -> AttemptRecord:
        return AttemptRecord(
            provider=candidate.config.provider,
            model=candidate.model,
            outcome=outcome,
            failure_code=code.value if code else None,
            fallback_from=candidate.fallback_from,
        )

    @staticmethod
    def _final(
        cause: GatewayFailure, code: FailureCode, attempts: list[AttemptRecord]
    ) -> GatewayFailure:
        return GatewayFailure(
            code,
            provider=cause.provider,
            model=cause.model,
            status=cause.status,
            attempts=tuple(attempts),
        )


class _InvalidOutput(Exception):
    def __init__(self, text: str):
        super().__init__("invalid output")
        self.text = text


class _Truncated(Exception):
    pass


class _Filtered(Exception):
    pass


def _parse(text: str, schema: type[BaseModel] | None) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("not an object")
    if schema is None:
        return payload
    return schema.model_validate(payload).model_dump()


def _describe(schema: type[BaseModel] | None) -> str:
    if schema is None:
        return "a single JSON object"
    return json.dumps(schema.model_json_schema(), sort_keys=True)


def _status(config: ProviderConfig) -> str:
    if config.last_failure_code:
        return "FAILING"
    if config.last_verified_at is None:
        return "UNVERIFIED"
    return "HEALTHY"


__all__ = [
    "AIGateway",
    "FailureSummary",
    "AccessDenied",
    "EmbeddingResult",
    "GatewayResult",
    "SettingsView",
    "VerificationReport",
    "mask",
]
