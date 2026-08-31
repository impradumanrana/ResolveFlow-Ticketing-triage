import json
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import KB_THRESHOLD, OPENAI_MODEL
from app.graph import build_workflow
from app.mcp_client import MCPClient
from app.knowledge_store import knowledge_stats, list_articles, prepare_demo_knowledge
from app.models import Ticket
from app.providers import DeterministicProvider, OpenAIProvider


RETRIEVAL_CASES = [
    {"query": "I cannot get into my profile because I forgot the passcode", "category": "technical", "expected": "KB-001"},
    {"query": "Where can I find a receipt for my purchase?", "category": "billing", "expected": "KB-012"},
    {"query": "The mobile program freezes and closes immediately", "category": "technical", "expected": "KB-006"},
    {"query": "How do I stop my membership renewing?", "category": "billing", "expected": "KB-007"},
    {"query": "My device will not pair with the workspace", "category": "technical", "expected": "KB-014"},
    {"query": "How can I change the card on file before renewal?", "category": "billing", "expected": "KB-015"},
    {"query": "How do I set up the one time password app?", "category": "technical", "expected": "KB-002"},
    {"query": "Someone else entered my profile without permission", "category": "account", "expected": "KB-009"},
    {"query": "The export is absent from my home screen", "category": "technical", "expected": "KB-011"},
]


def load_fixture(path: str):
    return json.loads(Path(__file__).resolve().parent.joinpath(path).read_text())


def run_golden_eval(provider=None, mcp_client=None):
    tickets = load_fixture("fixtures/tickets.json")
    golden = [ticket for ticket in tickets if ticket.get("golden")]
    provider = provider or DeterministicProvider()
    mcp_client = mcp_client or MCPClient()
    app = build_workflow(provider, mcp_client)

    results = []
    for ticket in golden:
        state = {
            "ticket": Ticket(**{k: v for k, v in ticket.items() if k not in {"expected_category", "expected_route", "golden"}}),
            "provider": provider,
            "mcp_client": mcp_client,
            "trace": [],
        }
        final = app.invoke(state)
        result = final["result"]
        mcp_event = next((event for event in result.trace if event.node == "kb_search_mcp"), None)
        grounded = bool(result.route != "AUTO_RESOLVE" or result.grounding_validated)
        results.append({
            "ticket_id": ticket["ticket_id"],
            "expected_category": ticket["expected_category"],
            "expected_route": ticket["expected_route"],
            "actual_category": result.category,
            "actual_route": result.route,
            "urgency": result.urgency,
            "processing_ms": result.processing_ms,
            "rule_codes": result.rule_codes,
            "kb_article": result.kb_match.article_id if result.kb_match else None,
            "kb_score": result.kb_match.score if result.kb_match else 0.0,
            "grounded": grounded,
            "mcp_trace_complete": bool(
                mcp_event
                and mcp_event.status == "success"
                and mcp_event.data.get("request")
                and "matches" in mcp_event.data
            ),
            "passed": (result.category == ticket["expected_category"] and result.route == ticket["expected_route"]),
        })

    return results


def knowledge_fingerprint(articles: list[dict[str, Any]]) -> str:
    values = [f"{item['article_id']}|{item.get('updated_at', '')}|{item.get('_source', '')}" for item in articles]
    return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()[:12]


def select_probe_articles(articles: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Choose a stable, source-aware sample from the actual operational corpus."""
    if not articles:
        return []
    imported = [item for item in articles if item.get("_source") != "Built-in"]
    built_in = [item for item in articles if item.get("_source") == "Built-in"]

    def spread(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        ordered = sorted(items, key=lambda item: item["article_id"])
        if len(ordered) <= count:
            return ordered
        if count == 1:
            return [ordered[len(ordered) // 2]]
        indexes = [round(index * (len(ordered) - 1) / (count - 1)) for index in range(count)]
        return [ordered[index] for index in indexes]

    imported_count = min(len(imported), 6 if built_in else limit)
    selected = spread(imported, imported_count)
    selected.extend(spread(built_in, min(len(built_in), limit - len(selected))))
    if len(selected) < limit:
        remaining = [item for item in articles if item["article_id"] not in {row["article_id"] for row in selected}]
        selected.extend(spread(remaining, min(len(remaining), limit - len(selected))))
    return selected[:limit]


def build_eval_report(
    provider=None,
    run_label: str = "Deterministic test classifier + real MCP stdio",
    mcp_client=None,
    retrieval_cases: list[dict[str, Any]] | None = None,
) -> dict:
    mcp_client = mcp_client or MCPClient()
    rows = run_golden_eval(provider, mcp_client)
    total = len(rows)
    expected_high_risk = [row for row in rows if row["expected_route"] == "ESCALATE"]
    route_counts = Counter(row["actual_route"] for row in rows)
    retrieval_client = mcp_client
    retrieval_rows = []
    active_retrieval_cases = RETRIEVAL_CASES if retrieval_cases is None else retrieval_cases
    for case in active_retrieval_cases:
        matches = retrieval_client.search(case["query"], case["category"], 3)
        top = matches[0] if matches else None
        expected = case.get("expected") or case.get("article_id")
        retrieval_rows.append({
            **case,
            "expected": expected,
            "actual": top.get("article_id") if top else None,
            "actual_title": top.get("title") if top else None,
            "score": top.get("score", 0.0) if top else 0.0,
            "above_threshold": bool(top and top.get("score", 0.0) >= KB_THRESHOLD),
            "passed": bool(top and top.get("article_id") == expected and top.get("score", 0.0) >= KB_THRESHOLD),
        })
    auto_rows = [row for row in rows if row["actual_route"] == "AUTO_RESOLVE"]
    return {
        "run_label": run_label,
        "total": total,
        "category_accuracy": round(sum(row["actual_category"] == row["expected_category"] for row in rows) / total, 4),
        "route_accuracy": round(sum(row["actual_route"] == row["expected_route"] for row in rows) / total, 4),
        "high_risk_recall": round(sum(row["actual_route"] == "ESCALATE" for row in expected_high_risk) / max(1, len(expected_high_risk)), 4),
        "unsafe_auto_resolves": sum(row["expected_route"] == "ESCALATE" and row["actual_route"] == "AUTO_RESOLVE" for row in rows),
        "retrieval_top1_accuracy": round(sum(row["passed"] for row in retrieval_rows) / max(1, len(retrieval_rows)), 4),
        "grounded_draft_rate": round(sum(row["grounded"] for row in auto_rows) / max(1, len(auto_rows)), 4),
        "mcp_trace_completeness": round(sum(row["mcp_trace_complete"] for row in rows) / total, 4),
        "average_latency_ms": round(sum(row["processing_ms"] for row in rows) / total),
        "route_distribution": dict(route_counts),
        "rows": rows,
        "retrieval_rows": retrieval_rows,
    }


def build_operational_eval_report(provider=None, mcp_client=None) -> dict:
    """Measure the configured model and the current user-managed Support knowledge base."""
    provider = provider or OpenAIProvider()
    mcp_client = mcp_client or MCPClient()
    articles = list_articles()
    if not articles:
        raise ValueError("The Support knowledge base is empty")
    sampled = select_probe_articles(articles)
    generated = provider.generate_retrieval_probes(sampled)
    article_by_id = {article["article_id"]: article for article in sampled}
    cases = [{
        "query": probe["query"],
        "category": probe["category"],
        "expected": probe["article_id"],
        "expected_title": article_by_id[probe["article_id"]]["title"],
        "source": article_by_id[probe["article_id"]].get("_source", "Unknown"),
    } for probe in generated]
    stats = knowledge_stats()
    report = build_eval_report(
        provider=provider,
        run_label=(
            f"OpenAI {OPENAI_MODEL} + current Support knowledge "
            f"({len(articles)} articles) + real MCP stdio"
        ),
        mcp_client=mcp_client,
        retrieval_cases=cases,
    )
    report.update({
        "evaluation_scope": "Current operational Support knowledge base",
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "knowledge_fingerprint": knowledge_fingerprint(articles),
        "knowledge_stats": stats,
        "knowledge_categories": dict(Counter(article["category"] for article in articles)),
        "knowledge_sources": dict(Counter(article.get("_source", "Unknown") for article in articles)),
        "sampled_article_ids": [article["article_id"] for article in sampled],
    })
    return report


if __name__ == "__main__":
    print(json.dumps(build_eval_report(
        provider=OpenAIProvider(),
        run_label=f"OpenAI {OPENAI_MODEL} + hybrid knowledge + real MCP stdio",
        mcp_client=MCPClient(kb_db_path=prepare_demo_knowledge()),
    ), indent=2))
