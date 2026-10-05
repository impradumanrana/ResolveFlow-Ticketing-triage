"""The MVP triage graph, running through the gateway.

`OpenAIProvider` holds the proven prompts and the evidence-key handling for
grounded drafts. Rewriting them to use the gateway would put that behaviour at
risk for no gain, so this subclass keeps every line of it and replaces only the
client object the prompts are sent through - the same seam the MVP's own tests
use.

A gateway failure propagates out of `classify` or `generate_grounded_answer`
as a `GatewayFailure`. The graph already turns any provider exception into
`MODEL_ERROR`, which C09's engine routes to a person. The specific code is kept
on `last_failure` for the trace.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from app.gateway.contract import Operation
from app.gateway.errors import GatewayFailure
from app.gateway.service import AIGateway, GatewayResult
from app.models import Classification
from app.providers import OpenAIProvider

GATEWAY_MODEL_LABEL = "client gateway"


class _Completions:
    def __init__(self, owner: GatewayProvider):
        self.owner = owner

    def create(self, *, messages: list[dict[str, str]], **_: Any) -> Any:
        # The model named by the MVP is ignored: the gateway, from the
        # client's approved configuration, decides which model runs.
        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        user = "\n".join(m["content"] for m in messages if m.get("role") == "user")
        return self.owner._send(system, user)


class _Client:
    def __init__(self, owner: GatewayProvider):
        self.chat = SimpleNamespace(completions=_Completions(owner))


class GatewayProvider(OpenAIProvider):
    def __init__(self, gateway: AIGateway, organization_id: str, correlation_id: str):
        # Deliberately not calling OpenAIProvider.__init__: it reads the MVP's
        # environment key, which the client deployment never uses.
        self.gateway = gateway
        self.organization_id = organization_id
        self.correlation_id = correlation_id
        self.client = _Client(self)  # type: ignore[assignment]
        self.model = GATEWAY_MODEL_LABEL
        self._operation = Operation.CLASSIFICATION
        self.results: list[GatewayResult] = []
        self.last_failure: GatewayFailure | None = None

    @contextmanager
    def _as(self, operation: Operation) -> Iterator[None]:
        previous = self._operation
        self._operation = operation
        try:
            yield
        finally:
            self._operation = previous

    def _send(self, system: str, user: str) -> Any:
        schema = Classification if self._operation is Operation.CLASSIFICATION else None
        try:
            result = self.gateway.complete(
                self.organization_id,
                self._operation,
                system=system,
                user=user,
                schema=schema,
                correlation_id=self.correlation_id,
            )
        except GatewayFailure as failure:
            self.last_failure = failure
            raise
        self.results.append(result)
        self.model = f"{result.provider}/{result.model}"
        content = json.dumps(result.data)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    def classify(self, ticket_text: str) -> dict[str, Any]:
        with self._as(Operation.CLASSIFICATION):
            return super().classify(ticket_text)

    def generate_grounded_answer(
        self, ticket_text: str, evidence: list[dict[str, Any]], customer_id: str | None = None
    ) -> dict[str, Any]:
        with self._as(Operation.GENERATION):
            return super().generate_grounded_answer(ticket_text, evidence, customer_id)

    def generate_retrieval_probes(self, articles: list[dict[str, Any]]) -> list[dict[str, str]]:
        with self._as(Operation.GENERATION):
            return super().generate_retrieval_probes(articles)

    def health_check(self) -> bool:
        return self.last_failure is None

    @property
    def rule_codes(self) -> tuple[str, ...]:
        codes: list[str] = []
        for result in self.results:
            codes.extend(result.rule_codes)
        return tuple(dict.fromkeys(codes))
