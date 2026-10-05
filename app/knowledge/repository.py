"""PostgreSQL + pgvector persistence and candidate generation.

Two rules govern every query here:

1. **Scoping happens in SQL, not after the fact.** Organization, publication
   status, and soft deletion are in the WHERE clause of both candidate queries.
   Filtering a result set in Python would still have pulled another tenant's
   passages into this process, and a later refactor that forgets the filter
   would silently start returning them.

2. **Nothing is interpolated into SQL.** Every value is a bound parameter.

The C05 gate requires that archived or unauthorized content cannot be
retrieved; these predicates are how that is true, and `tests/test_retrieval.py`
proves it against a live database.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Candidate breadth per arm. The MVP used 12; the same value keeps the
# rerank seeing a comparable pool.
DEFAULT_CANDIDATE_LIMIT = 12

# Only published, undeleted articles are retrievable. Archived content stays in
# the database for audit and restore, and must never reach a draft.
_RETRIEVABLE = """
    a.organization_id = :organization_id
    AND a.status = 'PUBLISHED'
    AND a.deleted_at IS NULL
    AND a.archived_at IS NULL
"""


@dataclass(frozen=True)
class CandidateChunk:
    """A retrieved passage with everything the reranker needs."""

    chunk_id: str
    article_id: str
    external_ref: str
    title: str
    category: str | None
    content: str
    start_offset: int
    end_offset: int
    semantic_similarity: float
    dense_rank: int | None
    lexical_rank: int | None
    text_rank: float


@dataclass(frozen=True)
class CorpusStatistics:
    """Corpus-level figures the BM25 component needs."""

    chunk_count: int
    average_chunk_tokens: float
    document_frequency: dict[str, int]


def _rows(connection: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    from sqlalchemy import text

    return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def dense_candidates(
    connection: Any,
    organization_id: str,
    query_embedding: Sequence[float],
    *,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    embedding_model: str,
) -> list[dict[str, Any]]:
    """Nearest passages by cosine distance, scoped in SQL."""
    literal = "[" + ",".join(f"{value:.8f}" for value in query_embedding) + "]"

    return _rows(
        connection,
        f"""
        SELECT c.id::text          AS chunk_id,
               a.id::text          AS article_id,
               a.external_ref,
               a.title,
               a.category,
               a.search_terms,
               c.content,
               c.start_offset,
               c.end_offset,
               1 - (e.embedding <=> CAST(:embedding AS vector)) AS similarity
          FROM knowledge_embeddings e
          JOIN knowledge_chunks c   ON c.id = e.chunk_id
          JOIN knowledge_articles a ON a.id = c.article_id
         WHERE {_RETRIEVABLE}
           AND e.organization_id = :organization_id
           AND e.embedding_model = :embedding_model
         ORDER BY e.embedding <=> CAST(:embedding AS vector)
         LIMIT :limit
        """,
        {
            "organization_id": organization_id,
            "embedding": literal,
            "embedding_model": embedding_model,
            "limit": limit,
        },
    )


_SAFE_TERM = re.compile(r"^[a-z0-9_]+$")


def build_or_tsquery(terms: Sequence[str]) -> str:
    """Join terms into an OR tsquery.

    `plainto_tsquery` ANDs every lexeme, so a natural-language question like
    "where can I find a receipt for my purchase" only matches a passage
    containing *all* of find, receipt and purchase - which in practice means no
    passage matches and the lexical arm silently contributes nothing.

    Retrieval wants "any of these terms, ranked", so the lexemes are ORed.
    Terms come from the shared tokeniser and are already restricted to
    `[a-z0-9_]`; anything else is dropped rather than escaped, because a term
    that cannot be validated has no business in a tsquery.
    """
    safe = [term for term in dict.fromkeys(terms) if _SAFE_TERM.match(term)]
    return " | ".join(safe)


def lexical_candidates(
    connection: Any,
    organization_id: str,
    terms: Sequence[str],
    *,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> list[dict[str, Any]]:
    """Best passages by PostgreSQL full-text rank, scoped in SQL."""
    or_query = build_or_tsquery(terms)
    if not or_query:
        return []

    return _rows(
        connection,
        f"""
        SELECT c.id::text          AS chunk_id,
               a.id::text          AS article_id,
               a.external_ref,
               a.title,
               a.category,
               a.search_terms,
               c.content,
               c.start_offset,
               c.end_offset,
               ts_rank_cd(c.search_vector, to_tsquery('english', :or_query)) AS text_rank
          FROM knowledge_chunks c
          JOIN knowledge_articles a ON a.id = c.article_id
         WHERE {_RETRIEVABLE}
           AND c.organization_id = :organization_id
           AND c.search_vector @@ to_tsquery('english', :or_query)
         ORDER BY text_rank DESC
         LIMIT :limit
        """,
        {"organization_id": organization_id, "or_query": or_query, "limit": limit},
    )


def corpus_statistics(
    connection: Any, organization_id: str, terms: Sequence[str]
) -> CorpusStatistics:
    """Figures BM25 needs, computed over the retrievable corpus only.

    Document frequency is fetched for the query's terms alone - a handful of
    indexed counts - rather than by scanning the whole corpus as the in-memory
    MVP did.
    """
    from sqlalchemy import text

    totals = connection.execute(
        text(
            f"""
            SELECT count(*)                                   AS chunk_count,
                   coalesce(avg(length(c.content) / 5.0), 1)  AS average_tokens
              FROM knowledge_chunks c
              JOIN knowledge_articles a ON a.id = c.article_id
             WHERE {_RETRIEVABLE}
               AND c.organization_id = :organization_id
            """
        ),
        {"organization_id": organization_id},
    ).one()

    frequency: dict[str, int] = {}
    unique_terms = [term for term in dict.fromkeys(terms) if term]
    for term in unique_terms:
        count = connection.execute(
            text(
                f"""
                SELECT count(*)
                  FROM knowledge_chunks c
                  JOIN knowledge_articles a ON a.id = c.article_id
                 WHERE {_RETRIEVABLE}
                   AND c.organization_id = :organization_id
                   AND c.search_vector @@ plainto_tsquery('english', :term)
                """
            ),
            {"organization_id": organization_id, "term": term},
        ).scalar_one()
        frequency[term] = int(count)

    return CorpusStatistics(
        chunk_count=int(totals[0]),
        average_chunk_tokens=float(totals[1]) or 1.0,
        document_frequency=frequency,
    )


def knowledge_fingerprint(connection: Any, organization_id: str) -> str:
    """Stable digest of the retrievable corpus.

    Stamped onto an evaluation run so a later corpus change invalidates the
    numbers visibly rather than leaving a stale pass on screen (D-014).
    """
    from sqlalchemy import text

    digest = connection.execute(
        text(
            f"""
            SELECT coalesce(
                     md5(string_agg(a.external_ref || ':' || a.version::text, ','
                         ORDER BY a.external_ref)),
                     'empty')
              FROM knowledge_articles a
             WHERE {_RETRIEVABLE}
            """
        ),
        {"organization_id": organization_id},
    ).scalar_one()
    return str(digest)


def retrievable_article_count(connection: Any, organization_id: str) -> int:
    from sqlalchemy import text

    return int(
        connection.execute(
            text(
                f"""
                SELECT count(*) FROM knowledge_articles a
                 WHERE {_RETRIEVABLE}
                """
            ),
            {"organization_id": organization_id},
        ).scalar_one()
    )
