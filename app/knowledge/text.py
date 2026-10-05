"""Shared text handling.

These are deliberately re-exported from `app.knowledge_store` rather than
copied. The retrieval formula depends on exact token sets, so a second
tokeniser that drifts by one stop word would change scores in ways that look
like a retrieval regression and are almost impossible to attribute later.

The underscore names are private to the MVP module; importing them here is an
explicit, recorded coupling (C-D046), not an accident.
"""

from __future__ import annotations

from app.knowledge_store import (
    CONCEPT_LOOKUP,
    STOP_WORDS,
    VALID_CATEGORIES,
    _fuzzy_coverage,
    _tokens,
    embed_texts,
    embedding_dimensions,
    embedding_model,
)

__all__ = [
    "CONCEPT_LOOKUP",
    "STOP_WORDS",
    "VALID_CATEGORIES",
    "embed_texts",
    "embedding_dimensions",
    "embedding_model",
    "fuzzy_coverage",
    "tokens",
]


def tokens(text: str, *, expand_concepts: bool = True) -> list[str]:
    """Tokenise exactly as the proven retrieval path does."""
    return _tokens(text, expand_concepts=expand_concepts)


def fuzzy_coverage(query_tokens: set[str], document_tokens: set[str]) -> float:
    """Fraction of query tokens covered, with the MVP's 0.84 similarity floor."""
    return _fuzzy_coverage(query_tokens, document_tokens)
