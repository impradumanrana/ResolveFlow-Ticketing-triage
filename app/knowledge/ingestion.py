"""Ingest approved sources into the organization's knowledge base.

The pipeline is: validate -> extract -> normalize -> chunk -> embed -> index,
with every stage's failure visible rather than silent. A source that fails
parsing is recorded as FAILED with its reason; a source that fails embedding
leaves its articles INDEXING rather than PUBLISHED, so they are not retrievable
and the operator can see why.

Deletion and reindexing live here too, because they share the invariant that
matters: a chunk's offsets must always slice back to the stored article body.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.knowledge.chunking import chunk_body, verify_offsets
from app.knowledge.embeddings import (
    embed_for_index,
    index_dimensions,
    index_embedding_model,
)
from app.knowledge.extraction import ExtractionError, extract

EMBEDDING_BATCH_SIZE = 64


@dataclass(frozen=True)
class IngestionResult:
    source_id: str | None
    filename: str
    articles_created: int
    chunks_created: int
    embeddings_created: int
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def ingest_file(
    connection: Any,
    organization_id: str,
    payload: bytes,
    filename: str,
    *,
    declared_content_type: str | None = None,
    default_category: str | None = None,
    uploaded_by_membership_id: str | None = None,
) -> IngestionResult:
    """Ingest one file. Never raises for a bad upload; reports it instead."""
    from sqlalchemy import text

    try:
        extraction = extract(
            payload,
            filename,
            declared_content_type=declared_content_type,
            default_category=default_category,
        )
    except ExtractionError as error:
        return IngestionResult(
            source_id=None,
            filename=filename,
            articles_created=0,
            chunks_created=0,
            embeddings_created=0,
            error=str(error),
        )

    digest = _sha256(payload)

    existing = connection.execute(
        text(
            "SELECT id::text FROM knowledge_sources "
            "WHERE organization_id = :organization_id AND sha256 = :sha256 "
            "AND deleted_at IS NULL"
        ),
        {"organization_id": organization_id, "sha256": digest},
    ).first()
    if existing:
        return IngestionResult(
            source_id=existing[0],
            filename=filename,
            articles_created=0,
            chunks_created=0,
            embeddings_created=0,
            error="This exact file has already been ingested.",
        )

    source_id = connection.execute(
        text(
            "INSERT INTO knowledge_sources "
            "(organization_id, filename, content_type, size_bytes, sha256, "
            " uploaded_by_membership_id, parse_state, article_count) "
            "VALUES (:organization_id, :filename, :content_type, :size_bytes, :sha256, "
            " :uploaded_by, 'PARSED', :article_count) RETURNING id::text"
        ),
        {
            "organization_id": organization_id,
            "filename": filename,
            "content_type": extraction.content_type,
            "size_bytes": len(payload),
            "sha256": digest,
            "uploaded_by": uploaded_by_membership_id,
            "article_count": len(extraction.articles),
        },
    ).scalar_one()

    articles = 0
    chunks = 0
    embeddings = 0

    for article in extraction.articles:
        article_id = connection.execute(
            text(
                "INSERT INTO knowledge_articles "
                "(organization_id, source_id, external_ref, title, body, category, "
                " search_terms, status) "
                "VALUES (:organization_id, :source_id, :external_ref, :title, :body, "
                " :category, :search_terms, 'INDEXING') "
                "ON CONFLICT (organization_id, external_ref) DO UPDATE "
                "SET title = EXCLUDED.title, body = EXCLUDED.body, "
                "    category = EXCLUDED.category, source_id = EXCLUDED.source_id, "
                "    search_terms = EXCLUDED.search_terms, "
                "    status = 'INDEXING', version = knowledge_articles.version + 1, "
                "    updated_at = now() "
                "RETURNING id::text"
            ),
            {
                "organization_id": organization_id,
                "source_id": source_id,
                "external_ref": article.external_ref,
                "title": article.title,
                "body": article.body,
                "category": article.category,
                "search_terms": list(article.search_terms),
            },
        ).scalar_one()
        articles += 1

        created, embedded = _index_article(
            connection, organization_id, article_id, article.body
        )
        chunks += created
        embeddings += embedded

    return IngestionResult(
        source_id=source_id,
        filename=filename,
        articles_created=articles,
        chunks_created=chunks,
        embeddings_created=embeddings,
        warnings=extraction.warnings,
    )


def _index_article(
    connection: Any, organization_id: str, article_id: str, body: str
) -> tuple[int, int]:
    """Chunk, embed, and publish one article. Replaces any previous chunks."""
    from sqlalchemy import text

    # Replacing rather than merging: a re-ingested article may chunk
    # differently, and a stale chunk would keep serving text that no longer
    # matches the stored body at those offsets.
    connection.execute(
        text("DELETE FROM knowledge_chunks WHERE article_id = :article_id"),
        {"article_id": article_id},
    )

    pieces = chunk_body(body)
    verify_offsets(body, pieces)
    if not pieces:
        connection.execute(
            text(
                "UPDATE knowledge_articles SET status = 'FAILED', "
                "index_error = 'No indexable text', updated_at = now() "
                "WHERE id = :article_id"
            ),
            {"article_id": article_id},
        )
        return 0, 0

    chunk_ids: list[str] = []
    for piece in pieces:
        chunk_id = connection.execute(
            text(
                "INSERT INTO knowledge_chunks "
                "(organization_id, article_id, chunk_index, content, start_offset, end_offset,"
                " token_count) "
                "VALUES (:organization_id, :article_id, :chunk_index, :content, :start_offset,"
                " :end_offset, :token_count) RETURNING id::text"
            ),
            {
                "organization_id": organization_id,
                "article_id": article_id,
                "chunk_index": piece.index,
                "content": piece.content,
                "start_offset": piece.start_offset,
                "end_offset": piece.end_offset,
                "token_count": max(1, len(piece.content) // 4),
            },
        ).scalar_one()
        chunk_ids.append(chunk_id)

    model = index_embedding_model()
    dimensions = index_dimensions()
    embedded = 0

    try:
        for start in range(0, len(pieces), EMBEDDING_BATCH_SIZE):
            batch = pieces[start : start + EMBEDDING_BATCH_SIZE]
            vectors = embed_for_index([piece.content for piece in batch])
            for chunk_id, vector in zip(chunk_ids[start:], vectors, strict=False):
                if len(vector) != dimensions:
                    raise ValueError(
                        f"Embedding model returned {len(vector)} dimensions, "
                        f"expected {dimensions}."
                    )
                connection.execute(
                    text(
                        "INSERT INTO knowledge_embeddings "
                        "(organization_id, chunk_id, embedding_model, dimensions, embedding) "
                        "VALUES (:organization_id, :chunk_id, :model, :dimensions, "
                        " CAST(:embedding AS vector))"
                    ),
                    {
                        "organization_id": organization_id,
                        "chunk_id": chunk_id,
                        "model": model,
                        "dimensions": dimensions,
                        "embedding": "[" + ",".join(f"{value:.8f}" for value in vector) + "]",
                    },
                )
                embedded += 1
    except Exception as error:
        # Leave the article unpublished. Half an index is worse than none:
        # retrieval would return some passages and silently miss others.
        connection.execute(
            text(
                "UPDATE knowledge_articles SET status = 'FAILED', "
                "index_error = :error, updated_at = now() WHERE id = :article_id"
            ),
            {"article_id": article_id, "error": f"{type(error).__name__}: {error}"[:500]},
        )
        return len(chunk_ids), embedded

    connection.execute(
        text(
            "UPDATE knowledge_articles SET status = 'PUBLISHED', indexed_at = now(), "
            "index_error = NULL, updated_at = now() WHERE id = :article_id"
        ),
        {"article_id": article_id},
    )
    return len(chunk_ids), embedded


def reindex_article(connection: Any, organization_id: str, external_ref: str) -> tuple[int, int]:
    """Rebuild chunks and embeddings for one article from its stored body."""
    from sqlalchemy import text

    row = connection.execute(
        text(
            "SELECT id::text, body FROM knowledge_articles "
            "WHERE organization_id = :organization_id AND external_ref = :external_ref "
            "AND deleted_at IS NULL"
        ),
        {"organization_id": organization_id, "external_ref": external_ref},
    ).first()
    if row is None:
        raise LookupError(f"No article {external_ref!r} in this organization.")

    return _index_article(connection, organization_id, row[0], row[1])


def archive_article(connection: Any, organization_id: str, external_ref: str) -> bool:
    """Withdraw an article from retrieval without destroying it."""
    from sqlalchemy import text

    return (
        connection.execute(
            text(
                "UPDATE knowledge_articles SET status = 'ARCHIVED', archived_at = now(), "
                "updated_at = now() "
                "WHERE organization_id = :organization_id AND external_ref = :external_ref "
                "AND deleted_at IS NULL AND archived_at IS NULL"
            ),
            {"organization_id": organization_id, "external_ref": external_ref},
        ).rowcount
        > 0
    )


CLEAR_ALL_CONFIRMATION = "DELETE ALL KNOWLEDGE"


def clear_all(connection: Any, organization_id: str, confirmation: str) -> int:
    """Delete every article in the organization. Requires an exact phrase.

    Typed confirmation, not a checkbox: this destroys the corpus the product
    answers from, and an accidental click is not a decision.
    """
    from sqlalchemy import text

    if confirmation != CLEAR_ALL_CONFIRMATION:
        raise ValueError(
            f"Clearing all knowledge requires the exact phrase "
            f"{CLEAR_ALL_CONFIRMATION!r}."
        )

    return connection.execute(
        text(
            "DELETE FROM knowledge_articles WHERE organization_id = :organization_id"
        ),
        {"organization_id": organization_id},
    ).rowcount
