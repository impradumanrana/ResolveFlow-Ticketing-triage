"""Embeddings for the production index.

Two requirements pull in different directions: the stored column is
`vector(1536)` and the check constraint enforces it, while tests must run with
no network and no paid model call.

The MVP's deterministic test embedder solves the second but produces 384
dimensions, which the production column rejects. So this module reuses the MVP's
exact hashing scheme at the production width. It is the same algorithm - token
and bigram features hashed with blake2b, signed by the low bit, L2 normalised -
widened from 384 to 1536.

Like the MVP's deterministic provider (D-012), it is reachable only when tests
explicitly set `RESOLVEFLOW_TEST_MODE`; nothing at runtime can select it.
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Sequence

from app.config import (
    HAS_OPENAI_KEY,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_DIMENSIONS,
    OPENAI_EMBEDDING_MODEL,
)
from app.knowledge.text import tokens

TEST_EMBEDDING_MODEL = "resolveflow-test-dense-v2"

# Bigrams outweigh single tokens: the MVP's value, kept so relative distances
# behave the same way.
BIGRAM_WEIGHT = 1.35


def index_dimensions() -> int:
    """Width of the stored vector column. Not test-mode dependent."""
    return int(OPENAI_EMBEDDING_DIMENSIONS)


def index_embedding_model() -> str:
    if os.getenv("RESOLVEFLOW_TEST_MODE") == "1":
        return TEST_EMBEDDING_MODEL
    return str(OPENAI_EMBEDDING_MODEL)


def deterministic_embedding(text: str, dimensions: int) -> list[float]:
    """Network-free dense vector. Test fixture only."""
    words = tokens(text)
    features: list[tuple[str, float]] = [(f"w:{word}", 1.0) for word in words]
    features.extend(
        (f"b:{left}_{right}", BIGRAM_WEIGHT)
        for left, right in zip(words, words[1:], strict=False)
    )

    vector = [0.0] * dimensions
    for feature, weight in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        vector[value % dimensions] += weight if value & 1 else -weight

    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def embed_for_index(texts: Sequence[str]) -> list[list[float]]:
    """Embed at the production width, or deterministically under test mode."""
    if not texts:
        return []

    dimensions = index_dimensions()

    if os.getenv("RESOLVEFLOW_TEST_MODE") == "1":
        return [deterministic_embedding(text, dimensions) for text in texts]

    if not HAS_OPENAI_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is required to create knowledge embeddings. "
            "Set RESOLVEFLOW_TEST_MODE=1 for offline tests."
        )

    from openai import OpenAI

    response = OpenAI(api_key=OPENAI_API_KEY, timeout=60.0, max_retries=2).embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=[text.replace("\n", " ") for text in texts],
        dimensions=dimensions,
        encoding_format="float",
    )
    return [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]
