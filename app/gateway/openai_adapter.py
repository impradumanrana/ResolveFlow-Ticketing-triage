"""OpenAI, the client's launch provider.

Uses the pinned SDK with its own retries switched off - the gateway retries,
once, visibly - and an explicit key on every client, so the SDK can never fall
back to an `OPENAI_API_KEY` it finds in the environment.

Regions are the SDK's own data-residency endpoints. A region the SDK does not
know is refused before any request is built.

Error mapping reads only the status and the provider's machine-readable
`code`. The human-readable message is never kept: for a rejected key it quotes
part of that key.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import httpx2
import openai
from openai import Omit

from app.gateway.contract import Completion, CompletionRequest, Embeddings
from app.gateway.credentials import SecretValue
from app.gateway.errors import FailureCode, GatewayFailure

R = TypeVar("R")

# The values accepted by the pinned SDK's `data_residency` argument.
OPENAI_REGIONS = frozenset({"global", "us", "eu", "ae"})


class OpenAIAdapter:
    name = "openai"
    supported_regions = OPENAI_REGIONS

    def __init__(self, http_client: httpx2.Client | None = None):
        # Injected in tests so the real SDK runs against a simulated server.
        self._http_client = http_client

    def _client(self, credential: SecretValue, region: str, timeout: float) -> openai.OpenAI:
        if region not in OPENAI_REGIONS:
            raise GatewayFailure(FailureCode.REGION_NOT_SUPPORTED, provider=self.name)
        key = credential.reveal()
        if not key:
            raise GatewayFailure(FailureCode.CREDENTIAL_MISSING, provider=self.name)
        return openai.OpenAI(
            api_key=key,
            data_residency=region,  # type: ignore[arg-type]
            max_retries=0,
            timeout=timeout,
            http_client=self._http_client,
            # `None` here makes the SDK read OPENAI_ORG_ID, OPENAI_PROJECT_ID,
            # and OPENAI_ADMIN_KEY from the environment, which could bill a
            # different OpenAI organization. Empty values stop the lookup, and
            # Omit removes the then-empty headers from the request.
            organization="",
            project="",
            admin_api_key="",
            default_headers={"OpenAI-Organization": Omit(), "OpenAI-Project": Omit()},
        )

    def complete(
        self,
        credential: SecretValue,
        *,
        model: str,
        region: str,
        request: CompletionRequest,
        timeout: float,
    ) -> Completion:
        client = self._client(credential, region, timeout)
        arguments: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.json_object:
            arguments["response_format"] = {"type": "json_object"}
        response, failure = _attempt(lambda: client.chat.completions.create(**arguments), model)
        if failure is not None or response is None:
            raise failure or GatewayFailure(FailureCode.PROVIDER_UNAVAILABLE, provider=self.name)

        if not response.choices:
            raise GatewayFailure(FailureCode.INVALID_SCHEMA, provider=self.name, model=model)
        choice = response.choices[0]
        usage = response.usage
        return Completion(
            text=choice.message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            finish_reason=choice.finish_reason,
        )

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
        client = self._client(credential, region, timeout)
        response, failure = _attempt(
            lambda: client.embeddings.create(
                model=model,
                input=[text.replace("\n", " ") for text in inputs],
                dimensions=dimensions,
            ),
            model,
        )
        if failure is not None or response is None:
            raise failure or GatewayFailure(FailureCode.PROVIDER_UNAVAILABLE, provider=self.name)
        vectors = [list(item.embedding) for item in sorted(response.data, key=lambda i: i.index)]
        usage = response.usage
        return Embeddings(vectors=vectors, prompt_tokens=usage.prompt_tokens if usage else None)

    def verify(self, credential: SecretValue, *, model: str, region: str, timeout: float) -> None:
        client = self._client(credential, region, timeout)
        # Retrieving a model is authenticated and model-specific, and
        # generates nothing, so verification is free.
        _, failure = _attempt(lambda: client.models.retrieve(model), model)
        if failure is not None:
            raise failure


def _attempt(call: Callable[[], R], model: str) -> tuple[R | None, GatewayFailure | None]:
    """Run an SDK call; return its result or a translated failure.

    The failure is raised by the caller *outside* the except block. Raising
    inside it - even with `from None` - leaves the SDK error attached as
    `__context__`, and that error carries the provider's message (which quotes
    the key) and the original request (which carries it in a header).
    """
    try:
        return call(), None
    except openai.OpenAIError as error:
        return None, translate(error, model)


def translate(error: Exception, model: str) -> GatewayFailure:
    """Map an SDK error to a code, reading only status and machine code."""
    provider = OpenAIAdapter.name
    if isinstance(error, openai.APITimeoutError):
        return GatewayFailure(FailureCode.PROVIDER_TIMEOUT, provider=provider, model=model)
    if isinstance(error, openai.APIConnectionError):
        return GatewayFailure(FailureCode.PROVIDER_UNAVAILABLE, provider=provider, model=model)
    if not isinstance(error, openai.APIStatusError):
        return GatewayFailure(FailureCode.PROVIDER_UNAVAILABLE, provider=provider, model=model)

    status = error.status_code
    code = str(getattr(error, "code", "") or "")

    def failure(failure_code: FailureCode, retry_after: float | None = None) -> GatewayFailure:
        return GatewayFailure(
            failure_code,
            provider=provider,
            model=model,
            status=status,
            retry_after_seconds=retry_after,
        )

    if status == 401:
        return failure(FailureCode.CREDENTIAL_INVALID)
    if status == 403:
        return failure(FailureCode.PERMISSION_DENIED)
    if status == 404:
        return failure(FailureCode.MODEL_UNAVAILABLE)
    if status == 429:
        if code == "insufficient_quota":
            return failure(FailureCode.QUOTA_EXHAUSTED)
        return failure(FailureCode.RATE_LIMITED, _retry_after(error))
    if status in (408, 409) or status >= 500:
        return failure(FailureCode.PROVIDER_UNAVAILABLE, _retry_after(error))
    return failure(FailureCode.REQUEST_REJECTED)


def _retry_after(error: openai.APIStatusError) -> float | None:
    raw = error.response.headers.get("retry-after")
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and value >= 0 else None
