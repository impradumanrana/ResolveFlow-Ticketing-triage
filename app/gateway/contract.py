"""The one contract every provider adapter implements.

An adapter translates, and nothing more. It does not retry, fall back, budget,
or validate structure - the gateway does all of that once, identically for
every provider. An adapter's only obligations are to call the provider, report
token usage honestly, and turn every failure into a `GatewayFailure` code
without carrying provider text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.gateway.credentials import SecretValue


class Operation(StrEnum):
    # Values match the C04 provider_usage.operation check constraint.
    CLASSIFICATION = "classification"
    GENERATION = "generation"
    EMBEDDING = "embedding"


@dataclass(frozen=True)
class CompletionRequest:
    system: str
    user: str
    max_output_tokens: int
    json_object: bool = True


@dataclass(frozen=True)
class Completion:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str | None = None


@dataclass(frozen=True)
class Embeddings:
    vectors: list[list[float]]
    prompt_tokens: int | None


class ProviderAdapter(Protocol):
    name: str
    supported_regions: frozenset[str]

    def complete(
        self,
        credential: SecretValue,
        *,
        model: str,
        region: str,
        request: CompletionRequest,
        timeout: float,
    ) -> Completion: ...

    def embed(
        self,
        credential: SecretValue,
        *,
        model: str,
        region: str,
        inputs: list[str],
        dimensions: int,
        timeout: float,
    ) -> Embeddings: ...

    def verify(self, credential: SecretValue, *, model: str, region: str, timeout: float) -> None:
        """Prove the key works for this model without generating anything billable."""
        ...
