from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

mcp = FastMCP("resolveflow_kb")

KB_ARTICLES = json.loads(
    Path(__file__).resolve().parent.joinpath("fixtures/faqs.json").read_text()
)


STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "for", "from", "how",
    "i", "if", "in", "is", "it", "me", "my", "of", "on", "or", "please", "the",
    "this", "to", "was", "what", "when", "where", "with", "you", "your",
}


def _normalize_tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if token not in STOP_WORDS and len(token) > 1
    }


def _fuzzy_overlap(query_tokens: set[str], article_tokens: set[str]) -> int:
    matches = 0
    remaining = set(article_tokens)
    for query_token in query_tokens:
        exact = query_token if query_token in remaining else None
        fuzzy = exact or next(
            (token for token in remaining if len(token) >= 5 and SequenceMatcher(None, query_token, token).ratio() >= 0.82),
            None,
        )
        if fuzzy:
            matches += 1
            remaining.discard(fuzzy)
    return matches


@mcp.tool()
def search_knowledge_base(query: str, category: str, top_k: int = 3) -> list[dict[str, Any]]:
    q_tokens = _normalize_tokens(query)
    results: list[dict[str, Any]] = []
    for article in KB_ARTICLES:
        if category and article["category"] != category:
            continue
        article_tokens = _normalize_tokens(article["title"] + " " + article["excerpt"])
        keyword_tokens = _normalize_tokens(" ".join(article.get("keywords", [])))
        overlap = _fuzzy_overlap(q_tokens, article_tokens | keyword_tokens)
        keyword_hits = sum(
            1 for phrase in article.get("keywords", [])
            if _normalize_tokens(phrase) and _normalize_tokens(phrase).issubset(q_tokens)
        )
        title_hits = _fuzzy_overlap(q_tokens, _normalize_tokens(article["title"]))
        score = min(0.99, overlap * 0.13 + keyword_hits * 0.17 + title_hits * 0.08)
        if not q_tokens or overlap == 0:
            score = 0.0
        results.append({
            "article_id": article["article_id"],
            "title": article["title"],
            "score": round(score, 3),
            "excerpt": article["excerpt"],
            "category": article["category"],
        })

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[: max(1, min(top_k, len(results)))]


if __name__ == "__main__":
    mcp.run(transport="stdio")
