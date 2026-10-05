"""Hybrid retrieval and rerank.

The scoring is the MVP's, preserved deliberately:

    score = 0.46*semantic + 0.28*lexical + 0.08*fuzzy
          + 0.08*keyword  + 0.06*category + 0.04*rrf

with the same corroboration bonus when a top lexical hit sits in the predicted
category, and the same fail-closed rule that zeroes a result with no semantic,
lexical, or keyword evidence at all.

What changed is where the components come from. Dense candidates now come from
pgvector and lexical candidates from PostgreSQL full text, both scoped to one
organization in SQL, and scoring happens at passage level rather than whole
article so a citation can name exact offsets.

Numeric parity with the MVP is therefore *not* expected and is not claimed:
the two score different units of text over different fields. What is claimed,
and measured by `evaluation.py` against labelled cases, is that retrieval
quality does not regress.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.knowledge import repository
from app.knowledge.embeddings import embed_for_index, index_embedding_model
from app.knowledge.text import fuzzy_coverage, tokens

# The MVP's weights. Changing any of these is a retrieval change and must be
# accompanied by a fresh evaluation run, not merely a passing test suite.
WEIGHT_SEMANTIC = 0.46
WEIGHT_LEXICAL = 0.28
WEIGHT_FUZZY = 0.08
WEIGHT_KEYWORD = 0.08
WEIGHT_CATEGORY = 0.06
WEIGHT_FUSION = 0.04

CORROBORATION_BONUS = 0.05
CORROBORATION_LEXICAL_FLOOR = 0.9
WEAK_SEMANTIC_FLOOR = 0.15
MAX_SCORE = 0.99

BM25_K1 = 1.5
BM25_B = 0.75
BM25_SATURATION = 2.5

RETRIEVAL_METHOD = "pgvector_dense_postgres_fts_hybrid_rerank"


@dataclass(frozen=True)
class RetrievalScores:
    semantic_similarity: float
    lexical_relevance: float
    fuzzy_coverage: float
    keyword_relevance: float
    category_preference: bool
    fusion_score: float
    dense_rank: int | None
    lexical_rank: int | None
    reranked_position: int = 0

    def as_trace(self) -> dict[str, Any]:
        """Evidence for the MCP trace. Score components, never reasoning."""
        return {
            "method": RETRIEVAL_METHOD,
            "semantic_similarity": round(self.semantic_similarity, 3),
            "lexical_relevance": round(self.lexical_relevance, 3),
            "fuzzy_coverage": round(self.fuzzy_coverage, 3),
            "keyword_relevance": round(self.keyword_relevance, 3),
            "category_preference": self.category_preference,
            "fusion_score": round(self.fusion_score, 3),
            "dense_rank": self.dense_rank,
            "lexical_rank": self.lexical_rank,
            "reranked_position": self.reranked_position,
            "embedding_model": index_embedding_model(),
            "vector_database": "PostgreSQL pgvector",
        }


@dataclass(frozen=True)
class Passage:
    """A retrieved passage, citable by exact offsets into the stored article."""

    chunk_id: str
    article_id: str
    external_ref: str
    title: str
    category: str | None
    content: str
    start_offset: int
    end_offset: int
    score: float
    scores: RetrievalScores = field(repr=False)

    def citation(self) -> dict[str, Any]:
        """The evidence a draft must carry to be groundable."""
        return {
            "article_ref": self.external_ref,
            "chunk_id": self.chunk_id,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "quote": self.content,
        }


def bm25(
    query_counts: Counter[str],
    document_terms: list[str],
    *,
    chunk_count: int,
    average_length: float,
    document_frequency: dict[str, int],
) -> float:
    """BM25 with the MVP's constants."""
    counts = Counter(document_terms)
    length = len(document_terms) or 1
    score = 0.0

    for term, query_frequency in query_counts.items():
        frequency = counts.get(term, 0)
        if not frequency:
            continue
        seen_in = max(1, document_frequency.get(term, 1))
        inverse_frequency = math.log(
            1 + (chunk_count - seen_in + 0.5) / (seen_in + 0.5)
        )
        denominator = frequency + BM25_K1 * (
            (1 - BM25_B) + BM25_B * length / max(1.0, average_length)
        )
        score += inverse_frequency * frequency * BM25_SATURATION / denominator * query_frequency

    return score


def _document_terms(candidate: dict[str, Any]) -> list[str]:
    """Terms scored for a passage.

    Mirrors the MVP's document: title twice, then curated search terms, then
    the text. The title is repeated because a passage whose heading names the
    subject is stronger evidence than one that merely mentions it in passing.
    """
    title = candidate.get("title") or ""
    curated = " ".join(candidate.get("search_terms") or [])
    return tokens(f"{title} {title} {curated} {candidate.get('content') or ''}")


def _keyword_terms(candidate: dict[str, Any]) -> set[str]:
    """Vocabulary a knowledge manager attached to the article, plus its title."""
    curated = candidate.get("search_terms") or []
    terms = set(tokens(" ".join(curated))) if curated else set()
    return terms | set(tokens(candidate.get("title") or ""))


def search(
    connection: Any,
    organization_id: str,
    query: str,
    *,
    category: str = "",
    top_k: int = 3,
    candidate_limit: int = repository.DEFAULT_CANDIDATE_LIMIT,
) -> list[Passage]:
    """Retrieve and rerank passages for one organization."""

    query_tokens = tokens(query)
    if not query_tokens:
        return []

    dense_rows = repository.dense_candidates(
        connection,
        organization_id,
        embed_for_index([query])[0],
        limit=candidate_limit,
        embedding_model=index_embedding_model(),
    )
    # Raw terms only: the tokeniser's synthetic `concept_*` tokens exist to
    # bridge paraphrases in the reranker and are not in the text index.
    lexical_rows = repository.lexical_candidates(
        connection,
        organization_id,
        tokens(query, expand_concepts=False),
        limit=candidate_limit,
    )

    if not dense_rows and not lexical_rows:
        return []

    merged: dict[str, dict[str, Any]] = {}
    dense_ranks: dict[str, int] = {}
    lexical_ranks: dict[str, int] = {}

    for rank, row in enumerate(dense_rows, start=1):
        merged[row["chunk_id"]] = dict(row)
        dense_ranks[row["chunk_id"]] = rank

    for rank, row in enumerate(lexical_rows, start=1):
        merged.setdefault(row["chunk_id"], dict(row)).update(
            {"text_rank": row.get("text_rank", 0.0)}
        )
        lexical_ranks[row["chunk_id"]] = rank

    statistics = repository.corpus_statistics(
        connection, organization_id, tokens(query, expand_concepts=False)
    )
    query_counts = Counter(query_tokens)

    raw_lexical: dict[str, float] = {}
    document_terms: dict[str, list[str]] = {}
    for chunk_id, candidate in merged.items():
        terms = _document_terms(candidate)
        document_terms[chunk_id] = terms
        raw_lexical[chunk_id] = bm25(
            query_counts,
            terms,
            chunk_count=max(1, statistics.chunk_count),
            average_length=statistics.average_chunk_tokens,
            document_frequency=statistics.document_frequency,
        )

    # Normalised against the strongest candidate, as the MVP normalises against
    # the strongest document.
    lexical_max = max(raw_lexical.values(), default=0.0) or 1.0

    passages: list[Passage] = []
    for chunk_id, candidate in merged.items():
        semantic = max(0.0, min(1.0, float(candidate.get("similarity") or 0.0)))
        lexical = raw_lexical[chunk_id] / lexical_max if raw_lexical[chunk_id] else 0.0
        fuzzy = fuzzy_coverage(set(query_tokens), set(document_terms[chunk_id]))

        keyword_pool = _keyword_terms(candidate)
        keyword_hits = len(keyword_pool & set(query_tokens))
        keyword = min(1.0, keyword_hits / max(1, min(3, len(keyword_pool) or 1)))

        category_match = 1.0 if category and candidate.get("category") == category else 0.0
        fusion = 0.5 / dense_ranks.get(chunk_id, 20) + 0.5 / lexical_ranks.get(chunk_id, 20)

        score = (
            WEIGHT_SEMANTIC * semantic
            + WEIGHT_LEXICAL * lexical
            + WEIGHT_FUZZY * fuzzy
            + WEIGHT_KEYWORD * keyword
            + WEIGHT_CATEGORY * category_match
            + WEIGHT_FUSION * fusion
        )
        if lexical >= CORROBORATION_LEXICAL_FLOOR and category_match:
            score += CORROBORATION_BONUS
        # Fail closed: no semantic, lexical, or keyword evidence is not a weak
        # match, it is no match. A non-zero score here would let an unrelated
        # passage clear the confidence threshold.
        if semantic < WEAK_SEMANTIC_FLOOR and lexical == 0 and keyword == 0:
            score = 0.0

        passages.append(
            Passage(
                chunk_id=chunk_id,
                article_id=candidate["article_id"],
                external_ref=candidate["external_ref"],
                title=candidate["title"],
                category=candidate.get("category"),
                content=candidate["content"],
                start_offset=int(candidate["start_offset"]),
                end_offset=int(candidate["end_offset"]),
                score=round(min(MAX_SCORE, score), 3),
                scores=RetrievalScores(
                    semantic_similarity=semantic,
                    lexical_relevance=lexical,
                    fuzzy_coverage=fuzzy,
                    keyword_relevance=keyword,
                    category_preference=bool(category_match),
                    fusion_score=fusion,
                    dense_rank=dense_ranks.get(chunk_id),
                    lexical_rank=lexical_ranks.get(chunk_id),
                ),
            )
        )

    passages.sort(key=lambda item: (item.score, item.scores.semantic_similarity), reverse=True)

    ranked: list[Passage] = []
    for position, passage in enumerate(passages, start=1):
        ranked.append(
            Passage(
                chunk_id=passage.chunk_id,
                article_id=passage.article_id,
                external_ref=passage.external_ref,
                title=passage.title,
                category=passage.category,
                content=passage.content,
                start_offset=passage.start_offset,
                end_offset=passage.end_offset,
                score=passage.score,
                scores=RetrievalScores(
                    **{
                        **passage.scores.__dict__,
                        "reranked_position": position,
                    }
                ),
            )
        )

    return ranked[: max(1, min(top_k, len(ranked)))]


def best_per_article(passages: list[Passage]) -> list[Passage]:
    """Collapse to the strongest passage per article, preserving order."""
    seen: set[str] = set()
    unique: list[Passage] = []
    for passage in passages:
        if passage.article_id in seen:
            continue
        seen.add(passage.article_id)
        unique.append(passage)
    return unique
