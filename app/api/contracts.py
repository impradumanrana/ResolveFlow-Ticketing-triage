from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models import Ticket, TriageResult

API_VERSION: Literal["v1"] = "v1"


class StrictContract(BaseModel):
    """Reject accidental fields so API drift fails visibly."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictContract):
    status: Literal["ok"]
    service: Literal["resolveflow-ai-api"] = "resolveflow-ai-api"
    version: Literal["v1"] = API_VERSION


class CapabilitiesResponse(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    deployment_profile: str
    # C14: the pilot stage this deployment is in. `observe_mode` used to be a
    # constant `true`, which stopped being true when C13 made drafting
    # possible; it is now derived from the posture, so a UAT step can confirm
    # it from outside rather than taking the deployment's word for it.
    pilot_stage: Literal["OBSERVE", "DRAFT"]
    observe_mode: bool
    # Not derived from anything. There is no configuration that turns sending
    # on, which is why this stays a literal here and a constraint in the
    # database.
    automatic_sending: Literal[False] = False
    routes: list[Literal["AUTO_RESOLVE", "CLARIFY", "ESCALATE"]]
    knowledge_transport: Literal["mcp"] = "mcp"


class TriageRequest(StrictContract):
    ticket: Ticket


class TriageEnvelope(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    organization_id: str = Field(min_length=3, max_length=80)
    request_id: str = Field(min_length=8, max_length=128)
    result: TriageResult


# ---------------------------------------------------------------------------
# Mailbox connection (C06)
# ---------------------------------------------------------------------------

MAILBOX_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]{2,79}$"


class MailboxConnectionStartResponse(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    mailbox_id: str = Field(pattern=MAILBOX_ID_PATTERN)
    attempt_id: str = Field(min_length=3, max_length=80)
    authorization_url: str = Field(min_length=20, max_length=4096)
    expires_at: datetime


class MailboxConnectionCompleteRequest(StrictContract):
    """Only the state and code. There is deliberately no mailbox field.

    The mailbox is taken from the stored attempt the state identifies. Because
    the contract forbids unknown fields, a callback that tries to name a mailbox
    is rejected before it reaches the service.
    """

    state: str = Field(min_length=16, max_length=512)
    code: str = Field(min_length=1, max_length=2048)


class MailboxConnectionOutcomeResponse(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    ok: bool
    code: str = Field(min_length=2, max_length=64)
    mailbox_id: str | None = None


# ---------------------------------------------------------------------------
# AI provider settings (C10). Masked metadata only: no key, no secret name.
# ---------------------------------------------------------------------------

PROVIDER_PATTERN = r"^[a-z][a-z0-9-]{1,31}$"


class AiProviderView(StrictContract):
    provider: str = Field(pattern=PROVIDER_PATTERN)
    status: Literal["HEALTHY", "FAILING", "UNVERIFIED"]
    credential_hint: str = Field(max_length=16)
    region: str | None
    models: dict[str, str]
    fallback: list[str]
    approved_models: list[str]
    budget_minor_units: int | None
    currency: str = Field(min_length=3, max_length=3)
    spent_minor_units: float = Field(ge=0)
    reserved_minor_units: float = Field(ge=0)
    last_verified_at: datetime | None
    last_failure_code: str | None
    last_failure_message: str | None
    last_failure_at: datetime | None


class AiFailureCount(StrictContract):
    code: str = Field(max_length=64)
    count: int = Field(ge=1)
    message: str | None = None


class AiSettingsResponse(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    providers: list[AiProviderView]
    recent_failures: list[AiFailureCount]
    fallback_uses: int = Field(default=0, ge=0)
    cost_note: str = (
        "Spend is ResolveFlow's estimate from the client's approved prices, "
        "not a balance reported by the provider."
    )


class AiCredentialReplaceRequest(StrictContract):
    """The new key. Shape is checked by the gateway, not here.

    No length or pattern constraint is declared on purpose: a failed schema
    constraint produces a validation error, and validation errors are exactly
    where frameworks like to repeat the rejected input.
    """

    api_key: SecretStr


class AiVerificationResponse(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    provider: str = Field(pattern=PROVIDER_PATTERN)
    ok: bool
    stored: bool
    code: str | None = Field(default=None, max_length=64)
    message: str | None = None
    checked_models: list[str]


class RefusalResponse(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    ok: Literal[False] = False
    code: str = Field(min_length=2, max_length=64)


# ---------------------------------------------------------------------------
# Quality Check (C11)
# ---------------------------------------------------------------------------


class QualityGateView(StrictContract):
    gate: str = Field(max_length=64)
    metric: str = Field(max_length=64)
    value: float | None
    threshold: float
    direction: Literal["min", "max"] = "min"
    passed: bool
    detail: dict[str, Any] = Field(default_factory=dict)


class QualityRunResponse(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    present: bool
    dataset_version: str | None = None
    threshold_version: str | None = None
    knowledge_fingerprint: str | None = None
    current_knowledge_fingerprint: str | None = None
    corpus_changed_since_run: bool = False
    article_count: int | None = None
    status: str | None = None
    passed: bool | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    gates: list[QualityGateView] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Human review and provider drafts (C12)
# ---------------------------------------------------------------------------

REVIEW_DECISIONS = Literal["EDIT", "APPROVE", "REJECT", "REROUTE", "ASSIGN", "RESOLVE"]
IDENTIFIER_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class ReviewRequestBody(StrictContract):
    decision: REVIEW_DECISIONS
    # The version the person was looking at. Required: a decision made against
    # a view that has since changed is refused, not applied.
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=200)
    reason: str | None = Field(default=None, max_length=2000)
    body: str | None = Field(default=None, max_length=20000)
    assignee_membership_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    queue_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    department_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)


class ProviderDraftView(StrictContract):
    status: Literal["PENDING", "CREATED", "FAILED", "REFUSED"]
    failure_code: str | None = Field(default=None, max_length=64)
    message: str | None = None
    provider_draft_id: str | None = Field(default=None, max_length=200)


class ReviewOutcomeResponse(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    ok: bool
    decision: REVIEW_DECISIONS
    code: str = Field(max_length=64)
    message: str
    ticket_version: int
    revision: int | None = None
    replayed: bool = False
    draft: ProviderDraftView | None = None


class DraftPreviewRequest(StrictContract):
    body: str | None = Field(default=None, max_length=20000)


class DraftPreviewResponse(StrictContract):
    schema_version: Literal["v1"] = API_VERSION
    from_address: str
    to: list[str]
    cc: list[str]
    subject: str
    body: str
    in_reply_to: str | None = None
    notes: list[str] = Field(default_factory=list)
    # What approving would do, said plainly, every time it is shown.
    effect: str = (
        "Approving creates a draft in the mailbox for a person to review and send. "
        "ResolveFlow never sends a message itself."
    )
    can_create_draft: bool = False
