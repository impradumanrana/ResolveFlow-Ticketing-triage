"""A provider that answers from the prompt, for offline runs.

Not a script of canned replies: classification comes from the MVP's own
deterministic classifier, and the grounded answer is built from the evidence
the prompt actually carries. So an offline Quality Check or demo measures the
real classifier, the real retrieval ranking, the real validator, and the real
rules - everything except the hosted model, which may not be called.

Reachable only when `RESOLVEFLOW_TEST_MODE=1`, like C05's offline embedder
(C-D054): nothing at runtime can select it in place of a real provider.
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.gateway.contract import Completion, CompletionRequest, Embeddings
from app.gateway.credentials import SecretValue
from app.providers import DeterministicProvider
from app.security.untrusted import unwrap_untrusted

CLASSIFY_MARKER = "Classify an incoming customer-support ticket"


class DeterministicAdapter:
    """Answers the gateway's calls without a network."""

    name = "sim"
    supported_regions = frozenset({"eu", "us"})

    def __init__(self) -> None:
        if os.getenv("RESOLVEFLOW_TEST_MODE") != "1":
            raise RuntimeError(
                "The offline provider is a test and demonstration affordance. "
                "Set RESOLVEFLOW_TEST_MODE=1 to use it."
            )
        self.classifier = DeterministicProvider()
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        credential: SecretValue,
        *,
        model: str,
        region: str,
        request: CompletionRequest,
        timeout: float,
    ) -> Completion:
        self.calls.append({"model": model, "system": request.system, "user": request.user})
        # A real model is told the <untrusted> block is data and reads what is
        # inside it. This stand-in has to do the same, or the C13 fence would
        # change the simulation rather than only the production prompt.
        content = unwrap_untrusted(request.user)
        text = (
            json.dumps(self.classifier.classify(content))
            if CLASSIFY_MARKER in request.system
            else json.dumps(_grounded(content))
        )
        return Completion(
            text=text,
            prompt_tokens=_tokens(request.system) + _tokens(request.user),
            completion_tokens=_tokens(text),
            finish_reason="stop",
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
        from app.knowledge.embeddings import deterministic_embedding

        return Embeddings(
            vectors=[deterministic_embedding(text, dimensions) for text in inputs],
            prompt_tokens=sum(_tokens(text) for text in inputs),
        )

    def verify(self, credential: SecretValue, *, model: str, region: str, timeout: float) -> None:
        return None


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _grounded(user_payload: str) -> dict[str, Any]:
    """Quote the first sentence of the first passage, exactly as supplied."""
    payload = json.loads(user_payload)
    evidence = payload.get("evidence") or []
    if not evidence:
        return {"answer": "", "citations": [], "claims": [], "sufficient_evidence": False}

    guidance = str(evidence[0]["approved_guidance"]).strip()
    key = evidence[0]["evidence_key"]
    sentence, separator, _ = guidance.partition(". ")
    quote = f"{sentence}." if separator else guidance
    return {
        "answer": f"{quote} [{key}]",
        "citations": [key],
        "claims": [{"claim": quote, "evidence_key": key, "support_quote": quote}],
        "sufficient_evidence": True,
    }
