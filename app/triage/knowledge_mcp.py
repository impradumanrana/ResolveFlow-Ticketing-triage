"""The knowledge base, served across the MCP stdio boundary.

The MVP served a local SQLite corpus over MCP. Production serves the client's
own PostgreSQL corpus through C05's hybrid retrieval, with the same tool name
and the same result shape, so the workflow above it is unchanged.

The boundary is kept, rather than replaced with a function call, because it is
what makes "the model can only see retrieved evidence" a structural fact: the
tool returns passages, and the passage text is what a draft must quote.

Run as `python -m app.triage.knowledge_mcp`.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

TOOL_NAME = "search_knowledge_base"
MAX_TOP_K = 10
# Fixture-backend constants; the production backend uses C05's own weights.
CATEGORY_BONUS = 0.06
SCORE_SATURATION = 2.0
RESULT_FIELDS = ("article_id", "title", "score", "excerpt", "category")

mcp = FastMCP("resolveflow_knowledge")


class BackendUnavailable(RuntimeError):
    """The server cannot serve knowledge. Never answered with an empty list."""


def _passage_result(passage: Any) -> dict[str, Any]:
    """C05's `Passage` in the shape the workflow and validator expect.

    `excerpt` is the exact stored chunk text: a draft's support quote has to be
    a substring of it, so shortening or reformatting here would silently break
    grounding validation.
    """
    return {
        "article_id": passage.external_ref,
        "title": passage.title,
        "score": round(float(passage.score), 6),
        "excerpt": passage.content,
        "category": passage.category or "",
        "source": passage.title,
        "retrieval": {
            "chunk_id": passage.chunk_id,
            "start_offset": passage.start_offset,
            "end_offset": passage.end_offset,
            "semantic": round(passage.scores.semantic, 6),
            "lexical": round(passage.scores.lexical, 6),
        },
    }


class PostgresBackend:
    """The client's corpus: C05 retrieval, scoped to one organization."""

    def __init__(self, database_url: str, organization_id: str):
        self.database_url = database_url
        self.organization_id = organization_id
        self._engine: Any = None

    def _connect(self) -> Any:
        if self._engine is None:
            from sqlalchemy import create_engine

            self._engine = create_engine(self.database_url, pool_pre_ping=True, pool_size=2)
        return self._engine

    def search(self, query: str, category: str, top_k: int) -> list[dict[str, Any]]:
        from app.knowledge import retrieval

        with self._connect().connect() as connection:
            passages = retrieval.search(
                connection, self.organization_id, query, category=category, top_k=top_k
            )
        return [_passage_result(passage) for passage in retrieval.best_per_article(passages)]


class FixtureBackend:
    """Test-only corpus from a JSON file.

    Reachable only when `RESOLVEFLOW_TEST_MODE=1`, like C05's deterministic
    embedder (C-D054). It ranks with the real BM25 function rather than an
    invented score, so the server under test behaves like the server in
    production: same tool, same shape, same failure modes - only the corpus and
    the ranking depth differ.
    """

    def __init__(self, path: Path):
        from app.knowledge.text import tokens

        self.articles = json.loads(Path(path).read_text(encoding="utf-8"))
        self.documents = [tokens(f"{a['title']} {a['excerpt']}") for a in self.articles]

    def search(self, query: str, category: str, top_k: int) -> list[dict[str, Any]]:
        from app.knowledge.retrieval import bm25
        from app.knowledge.text import tokens

        query_counts = Counter(tokens(query))
        if not query_counts:
            return []
        lengths = [len(document) for document in self.documents] or [1]
        frequency: dict[str, int] = {}
        for document in self.documents:
            for term in set(document):
                frequency[term] = frequency.get(term, 0) + 1

        scored: list[tuple[float, dict[str, Any]]] = []
        for article, document in zip(self.articles, self.documents, strict=True):
            score = bm25(
                query_counts,
                document,
                chunk_count=len(self.documents),
                average_length=sum(lengths) / len(lengths),
                document_frequency=frequency,
            )
            if score <= 0:
                continue
            # A category match is a bonus, never a filter - the same treatment
            # C05 gives it, so a misclassified conversation can still find its
            # article here exactly as it would in production.
            if category and article.get("category") == category:
                score *= 1 + CATEGORY_BONUS
            scored.append(
                (
                    score,
                    {
                        "article_id": article["article_id"],
                        "title": article["title"],
                        # Saturating transform into (0, 1): the ranking is what
                        # this backend is for; the scale is nominal.
                        "score": round(score / (score + SCORE_SATURATION), 6),
                        "excerpt": article["excerpt"],
                        "category": article.get("category", ""),
                        "source": article.get("source", article["title"]),
                        "retrieval": {"backend": "fixture"},
                    },
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]["article_id"]))
        return [result for _, result in scored[:top_k]]


def build_backend() -> Any:
    fixture = os.getenv("RESOLVEFLOW_MCP_FIXTURE", "").strip()
    if fixture:
        if os.getenv("RESOLVEFLOW_TEST_MODE") != "1":
            raise BackendUnavailable(
                "The fixture corpus is a test affordance and requires RESOLVEFLOW_TEST_MODE=1."
            )
        return FixtureBackend(Path(fixture))

    database_url = os.getenv("DATABASE_URL", "").strip()
    organization_id = os.getenv("RESOLVEFLOW_ORGANIZATION_ID", "").strip()
    if not database_url or not organization_id:
        raise BackendUnavailable(
            "Knowledge search needs DATABASE_URL and RESOLVEFLOW_ORGANIZATION_ID."
        )
    return PostgresBackend(database_url, organization_id)


_backend: Any = None


def backend() -> Any:
    global _backend
    if _backend is None:
        _backend = build_backend()
    return _backend


@mcp.tool()
def search_knowledge_base(query: str, category: str = "", top_k: int = 3) -> list[dict[str, Any]]:
    """Search the organization's approved knowledge and return citable passages."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("A query is required.")
    limit = max(1, min(int(top_k), MAX_TOP_K))
    results = backend().search(query.strip(), str(category or "").strip(), limit)
    for result in results:
        missing = [field for field in RESULT_FIELDS if field not in result]
        if missing:  # pragma: no cover - a backend bug, not a runtime path
            raise RuntimeError(f"Knowledge result is missing {', '.join(missing)}.")
    return results


if __name__ == "__main__":  # pragma: no cover - process entry point
    mcp.run(transport="stdio")
