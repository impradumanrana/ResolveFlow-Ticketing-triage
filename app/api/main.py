from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.ai_settings import router as ai_settings_router
from app.api.contracts import (
    API_VERSION,
    CapabilitiesResponse,
    HealthResponse,
    TriageEnvelope,
    TriageRequest,
)
from app.api.dependencies import InternalContext, require_internal_context
from app.api.limits import MAX_REQUEST_BYTES, RequestSizeLimit
from app.api.mailboxes import router as mailbox_router
from app.api.quality import router as quality_router
from app.api.review import router as review_router
from app.api.service import TriageServiceProtocol, get_triage_service
from app.pilot import describe as describe_pilot
from app.security.rate_limit import RateLimitExceeded, RateLimitUnavailable
from app.security.redaction import redact_exception


def create_app() -> FastAPI:
    profile = os.getenv("DEPLOYMENT_PROFILE", "local")
    expose_docs = profile in {"local", "test"}
    api = FastAPI(
        title="ResolveFlow AI Internal API",
        summary="Private service contract for grounded support-ticket triage.",
        version=API_VERSION,
        docs_url="/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if expose_docs else None,
    )

    # A body larger than this is refused before it is parsed. Nothing this
    # API accepts is close to the limit, and an unbounded body is a
    # denial-of-service primitive rather than a feature (C13).
    api.add_middleware(RequestSizeLimit, max_bytes=MAX_REQUEST_BYTES)

    def _refusal(
        code: str, *, status_code: int, headers: dict[str, str] | None = None
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"schema_version": API_VERSION, "ok": False, "code": code},
            headers=headers,
        )

    @api.exception_handler(RateLimitExceeded)
    async def rate_limited(_: Request, error: RateLimitExceeded) -> JSONResponse:
        return _refusal(
            error.code,
            status_code=429,
            headers={"Retry-After": str(error.retry_after_seconds)},
        )

    @api.exception_handler(RateLimitUnavailable)
    async def rate_limit_unavailable(_: Request, error: RateLimitUnavailable) -> JSONResponse:
        # Fail closed: the limiter could not count, so the request is refused.
        # Distinguished from the exceeded case because an operator needs to
        # tell a busy client from a broken counter.
        return _refusal(error.code, status_code=429, headers={"Retry-After": "5"})

    @api.exception_handler(Exception)
    async def unhandled(request: Request, error: Exception) -> JSONResponse:
        # The correlation id the web tier already sends is echoed so a person
        # reporting "it failed" can be joined to one log line. The error itself
        # is never returned, and what is logged is redacted (C13): a ticket
        # body or a credential in a traceback ends up in a log aggregator with
        # a different audience and a longer retention than the data itself.
        correlation = request.headers.get("x-request-id", "")
        logging.getLogger("resolveflow.api").error(
            "unhandled request failure request_id=%s path=%s detail=%s",
            correlation[:128],
            request.url.path,
            redact_exception(error),
        )
        return _refusal("INTERNAL_ERROR", status_code=500)

    @api.exception_handler(RequestValidationError)
    async def validation_failed(_: Request, error: RequestValidationError) -> JSONResponse:
        # FastAPI's default handler repeats the rejected input. Request bodies
        # here carry OAuth codes and API keys, so only the location and the
        # kind of problem are returned - never the value.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(item.get("loc", ())), "type": item.get("type", "invalid")}
                    for item in error.errors()
                ]
            },
        )

    @api.get("/healthz", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @api.get(
        "/v1/capabilities",
        response_model=CapabilitiesResponse,
        tags=["platform"],
    )
    def capabilities(
        _: InternalContext = Depends(require_internal_context),
    ) -> CapabilitiesResponse:
        posture = describe_pilot()
        return CapabilitiesResponse(
            deployment_profile=profile,
            pilot_stage=posture["pilot_stage"],
            observe_mode=posture["observe_mode"],
            routes=["AUTO_RESOLVE", "CLARIFY", "ESCALATE"],
        )

    @api.post(
        "/v1/triage",
        response_model=TriageEnvelope,
        tags=["triage"],
    )
    def triage(
        payload: TriageRequest,
        context: InternalContext = Depends(require_internal_context),
        service: TriageServiceProtocol = Depends(get_triage_service),
    ) -> TriageEnvelope:
        return TriageEnvelope(
            organization_id=context.organization_id,
            request_id=context.request_id,
            result=service.triage(payload.ticket),
        )

    api.include_router(mailbox_router)
    api.include_router(ai_settings_router)
    api.include_router(quality_router)
    api.include_router(review_router)

    return api


app = create_app()
