"""Internal endpoint for the latest Quality Check (C11).

Read-only. A run measures the client's own corpus with the client's own paid
model for every case, so it is started from the command line or a build step by
someone who has authorized that spend - not by a page load.

The response carries the corpus fingerprint the run measured *and* the corpus
fingerprint now, so the workspace can say plainly when yesterday's numbers
describe knowledge that has since changed (C04).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.contracts import QualityGateView, QualityRunResponse, RefusalResponse
from app.api.dependencies import InternalContext, require_internal_context
from app.api.mailboxes import _actor, _database_url, _engine

router = APIRouter(prefix="/v1/quality", tags=["quality"])

# Mirrors the TypeScript role matrix for quality.view.
VIEW_ROLES = frozenset({"OWNER", "ADMIN", "SUPERVISOR", "KNOWLEDGE_MANAGER", "AUDITOR"})


def get_engine() -> Any:
    url = _database_url()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Quality results are not configured.",
        )
    return _engine(url)


ContextDep = Annotated[InternalContext, Depends(require_internal_context)]
EngineDep = Annotated[Any, Depends(get_engine)]


@router.get(
    "/latest", response_model=QualityRunResponse, responses={403: {"model": RefusalResponse}}
)
def latest_run(context: ContextDep, engine: EngineDep) -> QualityRunResponse | JSONResponse:
    actor = _actor(context)
    if actor is None or actor.role not in VIEW_ROLES:
        return JSONResponse(
            status_code=403,
            content=RefusalResponse(
                code="ACTOR_REQUIRED" if actor is None else "ROLE_LACKS_PERMISSION"
            ).model_dump(mode="json"),
        )

    from app.knowledge import repository
    from app.triage.quality_store import PostgresQualityStore

    run = PostgresQualityStore(engine, context.organization_id).latest()
    with engine.connect() as connection:
        current = repository.knowledge_fingerprint(connection, context.organization_id)

    if run is None:
        return QualityRunResponse(present=False, current_knowledge_fingerprint=current)

    return QualityRunResponse(
        present=True,
        dataset_version=run["dataset_version"],
        threshold_version=run["threshold_version"],
        knowledge_fingerprint=run["knowledge_fingerprint"],
        current_knowledge_fingerprint=current,
        corpus_changed_since_run=run["knowledge_fingerprint"] != current,
        article_count=run["article_count"],
        status=run["status"],
        passed=run["passed"],
        started_at=run["started_at"],
        completed_at=run["completed_at"],
        gates=[
            QualityGateView(
                gate=result["gate"],
                metric=result["metric"],
                # -1 is how a metric that could not be computed is stored.
                value=None if float(result["value"]) < 0 else float(result["value"]),
                threshold=float(result["threshold"]),
                direction=(result["detail"] or {}).get("direction", "min"),
                passed=result["passed"],
                detail={k: v for k, v in (result["detail"] or {}).items() if k != "direction"},
            )
            for result in run["results"]
        ],
    )
