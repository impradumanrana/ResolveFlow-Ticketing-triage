from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from app.knowledge_store import initialize_database, list_articles, search_articles

mcp = FastMCP("resolveflow_kb")

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def load_kb_articles() -> list[dict[str, Any]]:
    """Compatibility loader used by diagnostics and tests."""
    initialize_database(fixture_dir=FIXTURE_DIR)
    return list_articles()


@mcp.tool()
def search_knowledge_base(query: str, category: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Search persistent knowledge with Qdrant dense vectors, BM25, and transparent reranking."""
    return search_articles(query, category, top_k)


if __name__ == "__main__":
    mcp.run(transport="stdio")
