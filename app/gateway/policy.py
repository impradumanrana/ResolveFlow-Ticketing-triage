"""What the client has approved, and what a call is allowed to try.

Approval is explicit per provider, model, operation, and region, and carries
the client's agreed prices. Prices are client inputs, not constants in this
file: provider price lists change, and a budget enforced against a stale list
is not enforced.

Money is counted in micro-units of the currency's minor unit (a millionth of a
cent) so a single call - often a fraction of a cent - is never rounded to zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.gateway.contract import Operation
from app.gateway.errors import FailureCode

MICRO = 1_000_000
# English averages about four characters per token. Three overstates the
# count, so the reservation is an upper bound rather than a guess.
CHARACTERS_PER_TOKEN_ESTIMATE = 3


@dataclass(frozen=True)
class ModelApproval:
    provider: str
    model: str
    operation: Operation
    region: str
    input_price_per_million_minor: int
    output_price_per_million_minor: int = 0
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ProviderConfig:
    organization_id: str
    provider: str
    credential_secret_name: str
    credential_last_four: str | None
    classification_model: str
    generation_model: str
    embedding_model: str
    embedding_dimensions: int
    region: str | None
    monthly_budget_minor_units: int | None
    currency: str = "USD"
    fallback_provider: str | None = None
    fallback_classification_model: str | None = None
    fallback_generation_model: str | None = None
    is_active: bool = True
    last_verified_at: datetime | None = None
    last_failure_code: str | None = None
    last_failure_at: datetime | None = None

    def model_for(self, operation: Operation) -> str:
        return {
            Operation.CLASSIFICATION: self.classification_model,
            Operation.GENERATION: self.generation_model,
            Operation.EMBEDDING: self.embedding_model,
        }[operation]

    def fallback_model_for(self, operation: Operation) -> str | None:
        return {
            Operation.CLASSIFICATION: self.fallback_classification_model,
            Operation.GENERATION: self.fallback_generation_model,
            Operation.EMBEDDING: None,
        }[operation]


@dataclass(frozen=True)
class Candidate:
    config: ProviderConfig
    model: str
    approval: ModelApproval
    fallback_from: str | None = None

    @property
    def label(self) -> str:
        return f"{self.config.provider}/{self.model}"


@dataclass(frozen=True)
class Route:
    candidates: tuple[Candidate, ...]
    rejected: tuple[tuple[str, FailureCode], ...] = field(default_factory=tuple)
    failure: FailureCode | None = None


def find_approval(
    approvals: list[ModelApproval], provider: str, model: str, operation: Operation, region: str
) -> ModelApproval | None:
    for approval in approvals:
        if (
            approval.provider == provider
            and approval.model == model
            and approval.operation == operation
            and approval.region == region
        ):
            return approval
    return None


def plan_route(
    configs: list[ProviderConfig],
    approvals: list[ModelApproval],
    operation: Operation,
    *,
    primary_provider: str | None = None,
) -> Route:
    """The ordered models a call may try. Pure, so every rule is testable."""
    active = {config.provider: config for config in configs if config.is_active}
    if not active:
        return Route((), failure=FailureCode.CONFIG_MISSING)

    if primary_provider is None:
        # The primary is the active configuration no other configuration names
        # as its fallback. More than one such candidate is ambiguous.
        named_as_fallback = {c.fallback_provider for c in active.values() if c.fallback_provider}
        roots = sorted(set(active) - named_as_fallback)
        if len(roots) != 1:
            return Route((), failure=FailureCode.CONFIG_MISSING)
        primary_provider = roots[0]

    primary = active.get(primary_provider)
    if primary is None:
        return Route((), failure=FailureCode.CONFIG_MISSING)
    if not primary.region:
        # No recorded region means no residency decision was made.
        return Route((), failure=FailureCode.REGION_NOT_SUPPORTED)

    region = primary.region
    first_model = primary.model_for(operation)
    first_approval = find_approval(approvals, primary.provider, first_model, operation, region)
    if first_approval is None:
        return Route((), failure=FailureCode.MODEL_NOT_APPROVED)

    candidates = [Candidate(primary, first_model, first_approval)]
    rejected: list[tuple[str, FailureCode]] = []

    # Embeddings never fall back: vectors from different models share a length
    # but not a meaning, and mixing them corrupts retrieval silently (C-D039).
    if operation is Operation.EMBEDDING:
        return Route(tuple(candidates))

    same_provider_model = primary.fallback_model_for(operation)
    if same_provider_model and same_provider_model != first_model:
        approval = find_approval(
            approvals, primary.provider, same_provider_model, operation, region
        )
        label = f"{primary.provider}/{same_provider_model}"
        if approval is None:
            rejected.append((label, FailureCode.MODEL_NOT_APPROVED))
        else:
            candidates.append(
                Candidate(primary, same_provider_model, approval, fallback_from=candidates[0].label)
            )

    if primary.fallback_provider:
        other = active.get(primary.fallback_provider)
        if other is None or other.provider == primary.provider:
            rejected.append((str(primary.fallback_provider), FailureCode.CONFIG_MISSING))
        elif other.region != region:
            # One approved region; no cross-region model fallback (CLIENT_SCOPE).
            rejected.append((other.provider, FailureCode.REGION_NOT_SUPPORTED))
        else:
            model = other.model_for(operation)
            approval = find_approval(approvals, other.provider, model, operation, region)
            if approval is None:
                rejected.append((f"{other.provider}/{model}", FailureCode.MODEL_NOT_APPROVED))
            else:
                candidates.append(
                    Candidate(other, model, approval, fallback_from=candidates[0].label)
                )

    return Route(tuple(candidates), tuple(rejected))


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / CHARACTERS_PER_TOKEN_ESTIMATE))


def cost_micro(approval: ModelApproval, prompt_tokens: int, completion_tokens: int) -> int:
    """Exact integer cost: tokens x (minor units per million) = micro-minor units."""
    return (
        prompt_tokens * approval.input_price_per_million_minor
        + completion_tokens * approval.output_price_per_million_minor
    )


def reservation_micro(approval: ModelApproval, prompt_text: str, max_output_tokens: int) -> int:
    return cost_micro(approval, estimate_tokens(prompt_text), max_output_tokens)


def budget_period(moment: datetime) -> date:
    """Budgets run by calendar month in UTC, like provider billing."""
    if moment.tzinfo is None:
        raise ValueError("Budget periods require an aware timestamp.")
    utc = moment.astimezone(UTC)
    return date(utc.year, utc.month, 1)
