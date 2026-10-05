"""Shared fixtures for the C10 gateway tests: a scripted provider and secret store."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from app.gateway.contract import Completion, CompletionRequest, Embeddings, Operation
from app.gateway.credentials import CredentialCache, SecretValue
from app.gateway.errors import FailureCode, GatewayFailure
from app.gateway.memory_store import InMemoryGatewayStore
from app.gateway.policy import ModelApproval, ProviderConfig
from app.gateway.service import AIGateway
from app.mailbox.access import Actor

ORG = "00000000-0000-0000-0000-0000000000a1"
OTHER_ORG = "00000000-0000-0000-0000-0000000000b2"
KEY = "sk-live-client-key-AAAA-BBBB-CCCC-1234"
NEW_KEY = "sk-live-client-key-DDDD-EEEE-FFFF-5678"
SECRET = "projects/client-prod/secrets/resolveflow-prod-llm-provider-api-key"
OTHER_SECRET = "projects/client-prod/secrets/resolveflow-prod-llm-fallback-api-key"
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
GOOD_CLASSIFICATION = (
    '{"category":"technical","urgency":"low","confidence":0.91,"queue":"Tier-1 Technical"}'
)
OWNER = Actor("m-owner", ORG, "OWNER", "ACTIVE")


def config(**overrides: Any) -> ProviderConfig:
    base = ProviderConfig(
        organization_id=ORG,
        provider="sim",
        credential_secret_name=SECRET,
        credential_last_four=None,
        classification_model="sim-small",
        generation_model="sim-large",
        embedding_model="sim-embed",
        embedding_dimensions=1536,
        region="eu",
        monthly_budget_minor_units=10_000,  # 100.00
    )
    return replace(base, **overrides)


def approvals(provider: str = "sim", region: str = "eu", **prices: int) -> list[ModelApproval]:
    input_price = prices.get("input", 100)  # 1.00 per million tokens
    output_price = prices.get("output", 400)
    return [
        ModelApproval(
            provider, "sim-small", Operation.CLASSIFICATION, region, input_price, output_price, 400
        ),
        ModelApproval(
            provider,
            "sim-small-2",
            Operation.CLASSIFICATION,
            region,
            input_price,
            output_price,
            400,
        ),
        ModelApproval(
            provider, "sim-large", Operation.GENERATION, region, input_price, output_price, 1500
        ),
        ModelApproval(provider, "sim-embed", Operation.EMBEDDING, region, input_price, 0, None),
    ]


def completion(
    text: str = GOOD_CLASSIFICATION,
    prompt: int | None = 120,
    done: int | None = 30,
    finish: str = "stop",
) -> Completion:
    return Completion(text, prompt, done, finish)


def fail(
    code: FailureCode, *, status: int | None = None, retry_after: float | None = None
) -> GatewayFailure:
    return GatewayFailure(code, provider="sim", status=status, retry_after_seconds=retry_after)


@dataclass
class ScriptedAdapter:
    """Answers from a per-model script; the last entry repeats."""

    name: str = "sim"
    supported_regions: frozenset[str] = frozenset({"eu", "us"})
    scripts: dict[str, list[Any]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    verified: list[tuple[str, str]] = field(default_factory=list)
    verify_script: dict[str, Any] = field(default_factory=dict)

    def _next(self, model: str) -> Any:
        script = self.scripts.get(model) or [completion()]
        outcome = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def complete(
        self,
        credential: SecretValue,
        *,
        model: str,
        region: str,
        request: CompletionRequest,
        timeout: float,
    ) -> Completion:
        self.calls.append(
            {
                "model": model,
                "region": region,
                "key": credential.reveal(),
                "request": request,
                "timeout": timeout,
            }
        )
        return self._next(model)

    def embed(
        self,
        credential: SecretValue,
        *,
        model: str,
        region: str,
        inputs: list[str],
        dimensions: int,
        timeout: float,
    ) -> Embeddings:
        self.calls.append(
            {
                "model": model,
                "region": region,
                "key": credential.reveal(),
                "inputs": inputs,
                "dimensions": dimensions,
            }
        )
        outcome = self._next(model)
        if isinstance(outcome, Completion):
            return Embeddings([[0.1] * dimensions for _ in inputs], prompt_tokens=len(inputs) * 5)
        return outcome

    def verify(self, credential: SecretValue, *, model: str, region: str, timeout: float) -> None:
        self.verified.append((model, credential.reveal()))
        outcome = self.verify_script.get(credential.reveal())
        if isinstance(outcome, BaseException):
            raise outcome


@dataclass
class FakeSecrets:
    versions: dict[str, list[str]] = field(default_factory=lambda: {SECRET: [KEY]})
    failures: dict[str, FailureCode] = field(default_factory=dict)
    add_failure: FailureCode | None = None
    reads: int = 0

    def access(self, secret_name: str) -> SecretValue:
        self.reads += 1
        if secret_name in self.failures:
            raise GatewayFailure(self.failures[secret_name])
        stored = self.versions.get(secret_name)
        if not stored:
            raise GatewayFailure(FailureCode.CREDENTIAL_MISSING)
        return SecretValue(stored[-1])

    def add_version(self, secret_name: str, value: SecretValue) -> str:
        if self.add_failure:
            raise GatewayFailure(self.add_failure)
        self.versions.setdefault(secret_name, []).append(value.reveal())
        return f"{secret_name}/versions/{len(self.versions[secret_name])}"


@dataclass
class Clock:
    now: datetime = NOW
    ticks: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        self.ticks += 0.05
        return self.ticks

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


@dataclass
class Harness:
    gateway: AIGateway
    store: InMemoryGatewayStore
    adapter: ScriptedAdapter
    secrets: FakeSecrets
    clock: Clock
    others: dict[str, ScriptedAdapter] = field(default_factory=dict)

    def calls_by(self, **match: Any) -> list[Any]:
        return [c for c in self.store.calls if all(getattr(c, k) == v for k, v in match.items())]

    def ledger(self, provider: str = "sim") -> list[int]:
        return self.store.ledgers.get((ORG, provider, NOW.date().replace(day=1)), [0, 0])


def harness(
    configs: list[ProviderConfig] | None = None,
    approved: list[ModelApproval] | None = None,
    scripts: dict[str, list[Any]] | None = None,
    extra_adapters: dict[str, ScriptedAdapter] | None = None,
    secrets: FakeSecrets | None = None,
) -> Harness:
    store = InMemoryGatewayStore(
        configs=configs if configs is not None else [config()],
        approvals=approved if approved is not None else approvals(),
    )
    adapter = ScriptedAdapter(scripts=defaultdict(list, scripts or {}))
    secrets = secrets or FakeSecrets()
    clock = Clock()
    adapters: dict[str, Any] = {"sim": adapter, **(extra_adapters or {})}
    gateway = AIGateway(
        store,
        CredentialCache(secrets, clock=clock.monotonic),
        adapters,
        now=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        index_dimensions=1536,
    )
    return Harness(gateway, store, adapter, secrets, clock, extra_adapters or {})


def classify(h: Harness, correlation_id: str = "c-1", **kwargs: Any):
    from app.models import Classification

    return h.gateway.complete(
        ORG,
        Operation.CLASSIFICATION,
        system="Classify.",
        user="My app crashes.",
        correlation_id=correlation_id,
        schema=Classification,
        **kwargs,
    )
