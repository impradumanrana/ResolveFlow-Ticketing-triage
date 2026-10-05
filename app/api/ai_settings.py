"""Internal endpoints for the client's AI provider settings (C10).

What leaves this service is masked metadata: provider, models, region, budget,
estimated spend, status, a failure code with a plain message, and at most the
last four characters of the key. Never the key, never the Secret Manager name.

The new key arrives once, is checked against the provider, and is written to
Secret Manager only if it works. It is held as `SecretStr` so no default repr,
log line, or error handler can print it.

Like the mailbox endpoints, refusals are returned rather than raised so the
audit event written during the request commits with it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import JSONResponse

from app.api.contracts import (
    PROVIDER_PATTERN,
    AiCredentialReplaceRequest,
    AiFailureCount,
    AiProviderView,
    AiSettingsResponse,
    AiVerificationResponse,
    RefusalResponse,
)
from app.api.dependencies import InternalContext, require_internal_context
from app.api.limits import limit_ai_settings_write, limit_credential_verify
from app.api.mailboxes import _actor, _database_url, _engine
from app.gateway.credentials import CredentialCache, SecretManagerSource
from app.gateway.errors import GatewayFailure, failure_message
from app.gateway.openai_adapter import OpenAIAdapter
from app.gateway.service import AccessDenied, AIGateway, VerificationReport

router = APIRouter(prefix="/v1/ai", tags=["ai-settings"])


@lru_cache(maxsize=1)
def _credentials() -> CredentialCache:
    # One cache per process, so a verified key is not re-read on every request
    # and a replaced key is invalidated for every later request.
    return CredentialCache(SecretManagerSource())


def get_gateway() -> AIGateway:
    url = _database_url()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI settings are not configured.",
        )
    from app.gateway.store import PostgresGatewayStore

    # The store takes the engine, not a request transaction: each write commits
    # on its own, so a refusal's audit event survives and no transaction is
    # held open while a provider is being called.
    return AIGateway(
        PostgresGatewayStore(_engine(url)),
        _credentials(),
        {"openai": OpenAIAdapter()},
    )


ContextDep = Annotated[InternalContext, Depends(require_internal_context)]
GatewayDep = Annotated[AIGateway, Depends(get_gateway)]
ProviderPath = Annotated[str, Path(pattern=PROVIDER_PATTERN)]
REFUSALS: dict[int | str, dict[str, object]] = {
    403: {"model": RefusalResponse},
    404: {"model": RefusalResponse},
    # C13 rate limit: changing models, budgets or keys is a deliberate act,
    # and verification spends money with the provider.
    429: {"model": RefusalResponse},
}


def _refusal(code: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status, content=RefusalResponse(code=code).model_dump(mode="json")
    )


def _report(report: VerificationReport) -> AiVerificationResponse:
    return AiVerificationResponse(
        provider=report.provider,
        ok=report.ok,
        stored=report.stored,
        code=report.code,
        message=failure_message(report.code),
        checked_models=list(report.checked_models),
    )


@router.get("/settings", response_model=AiSettingsResponse, responses=REFUSALS)
def read_settings(context: ContextDep, gateway: GatewayDep) -> AiSettingsResponse | JSONResponse:
    actor = _actor(context)
    if actor is None:
        return _refusal("ACTOR_REQUIRED", 403)
    try:
        view = gateway.describe(actor, context.organization_id)
    except AccessDenied as denied:
        return _refusal(denied.code, 403)
    return AiSettingsResponse(
        providers=[
            AiProviderView(
                provider=p.provider,
                status=p.status,  # type: ignore[arg-type]
                credential_hint=p.credential_hint,
                region=p.region,
                models=p.models,
                fallback=list(p.fallback),
                approved_models=list(p.approved_models),
                budget_minor_units=p.budget_minor_units,
                currency=p.currency,
                spent_minor_units=p.spent_minor_units,
                reserved_minor_units=p.reserved_minor_units,
                last_verified_at=p.last_verified_at,
                last_failure_code=p.last_failure_code,
                last_failure_message=p.last_failure_message,
                last_failure_at=p.last_failure_at,
            )
            for p in view.providers
        ],
        recent_failures=[
            AiFailureCount(code=f.code, count=f.count, message=f.message)
            for f in view.recent_failures
        ],
        fallback_uses=view.fallback_uses,
    )


@router.post(
    "/providers/{provider}/verify",
    response_model=AiVerificationResponse,
    responses=REFUSALS,
    dependencies=[Depends(limit_credential_verify)],
)
def verify_provider(
    provider: ProviderPath, context: ContextDep, gateway: GatewayDep
) -> AiVerificationResponse | JSONResponse:
    actor = _actor(context)
    if actor is None:
        return _refusal("ACTOR_REQUIRED", 403)
    try:
        report = gateway.verify(actor, context.organization_id, provider)
    except AccessDenied as denied:
        return _refusal(denied.code, 403)
    except GatewayFailure as failure:
        return _refusal(failure.code.value, 404)
    return _report(report)


@router.put(
    "/providers/{provider}/credential",
    response_model=AiVerificationResponse,
    responses=REFUSALS,
    dependencies=[Depends(limit_ai_settings_write)],
)
def replace_credential(
    provider: ProviderPath,
    payload: AiCredentialReplaceRequest,
    context: ContextDep,
    gateway: GatewayDep,
) -> AiVerificationResponse | JSONResponse:
    actor = _actor(context)
    if actor is None:
        return _refusal("ACTOR_REQUIRED", 403)
    try:
        report = gateway.replace_credential(
            actor, context.organization_id, provider, payload.api_key.get_secret_value()
        )
    except AccessDenied as denied:
        return _refusal(denied.code, 403)
    except GatewayFailure as failure:
        return _refusal(failure.code.value, 404)
    return _report(report)
