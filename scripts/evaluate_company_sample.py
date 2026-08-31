"""Run a live, reproducible company-corpus evaluation through OpenAI and MCP."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from app.cli import triage_ticket
from app.config import KB_THRESHOLD
from app.knowledge_store import knowledge_stats, replace_articles
from app.mcp_client import MCPClient
from app.models import Ticket
from app.providers import OpenAIProvider


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_CSV = ROOT / "sample_data" / "northstar_ecommerce_knowledge.csv"
TICKET_CSV = ROOT / "sample_data" / "northstar_support_tickets_50.csv"
EVAL_DB = ROOT / "app" / "data" / "company-eval" / "knowledge.db"
REPORT_PATH = ROOT / "app" / "data" / "company-eval" / "latest-report.json"

RETRIEVAL_CASES = [
    ("The courier says it arrived but nobody here can find the box", "account", "NSM-003"),
    ("Can I swap these unworn trainers for another size?", "billing", "NSM-007"),
    ("My bank still has no money twelve working days after refund confirmation", "billing", "NSM-008"),
    ("I see the purchase twice as completed transactions", "billing", "NSM-011"),
    ("Where is the tax receipt for my paid order?", "billing", "NSM-013"),
    ("The verification app codes stopped working after I changed phones", "technical", "NSM-022"),
    ("Someone signed in from overseas and bought something", "account", "NSM-024"),
    ("The iPhone app exits whenever I reach payment", "technical", "NSM-026"),
    ("My headphones failed four months after purchase", "account", "NSM-028"),
    ("Rewards never appeared after the return period ended", "billing", "NSM-029"),
    ("We need a formal quote for two hundred monitors", "account", "NSM-030"),
]

TRIAGE_IDS = [
    "SHOP-001", "SHOP-006", "SHOP-017", "SHOP-032", "SHOP-041",
    "SHOP-020", "SHOP-036", "SHOP-045", "SHOP-048", "SHOP-050",
]


def load_knowledge() -> list[dict]:
    with KNOWLEDGE_CSV.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return [{
        "article_id": row["article_id"].strip(),
        "title": row["title"].strip(),
        "category": row["category"].strip().lower(),
        "excerpt": row["excerpt"].strip(),
        "keywords": [value.strip() for value in row.get("keywords", "").split("|") if value.strip()],
    } for row in rows]


def load_tickets() -> dict[str, dict]:
    with TICKET_CSV.open(newline="", encoding="utf-8-sig") as handle:
        return {row["ticket_id"]: row for row in csv.DictReader(handle)}


def main() -> None:
    knowledge = load_knowledge()
    indexed = replace_articles(knowledge, source="Northstar company evaluation", db_path=EVAL_DB)
    client = MCPClient(kb_db_path=EVAL_DB)

    retrieval_rows = []
    for query, category, expected in RETRIEVAL_CASES:
        matches = client.search(query, category, 3)
        top = matches[0] if matches else None
        retrieval_rows.append({
            "query": query,
            "expected": expected,
            "actual": top.get("article_id") if top else None,
            "score": top.get("score", 0.0) if top else 0.0,
            "above_answer_threshold": bool(top and top.get("score", 0.0) >= KB_THRESHOLD),
            "passed": bool(top and top.get("article_id") == expected),
        })

    provider = OpenAIProvider()
    source_tickets = load_tickets()
    triage_rows = []
    for ticket_id in TRIAGE_IDS:
        raw = source_tickets[ticket_id]
        result = triage_ticket(Ticket(
            ticket_id=raw["ticket_id"],
            customer_id=raw.get("customer_id") or None,
            subject=raw["subject"],
            body=raw["body"],
        ), provider=provider, mcp_client=client)
        mcp_event = next((event for event in result.trace if event.node == "kb_search_mcp"), None)
        triage_rows.append({
            "ticket_id": ticket_id,
            "category": result.category,
            "urgency": result.urgency,
            "route": result.route,
            "article": result.kb_match.article_id if result.kb_match else None,
            "score": result.kb_match.score if result.kb_match else 0.0,
            "grounded": result.grounding_validated,
            "mcp_trace_complete": bool(mcp_event and mcp_event.status == "success" and mcp_event.data.get("request")),
            "rule_codes": result.rule_codes,
            "processing_ms": result.processing_ms,
        })

    risky_ids = {"SHOP-020", "SHOP-036", "SHOP-045", "SHOP-048", "SHOP-050"}
    unsafe_auto_resolves = sum(row["ticket_id"] in risky_ids and row["route"] == "AUTO_RESOLVE" for row in triage_rows)
    retrieval_accuracy = sum(row["passed"] for row in retrieval_rows) / len(retrieval_rows)
    report = {
        "corpus": knowledge_stats(EVAL_DB),
        "articles_indexed": indexed,
        "retrieval_top1_accuracy": round(retrieval_accuracy, 4),
        "retrieval_above_answer_threshold_rate": round(
            sum(row["above_answer_threshold"] for row in retrieval_rows) / len(retrieval_rows), 4
        ),
        "unsafe_auto_resolves": unsafe_auto_resolves,
        "complete_mcp_traces": sum(row["mcp_trace_complete"] for row in triage_rows),
        "grounded_auto_resolves": all(row["grounded"] for row in triage_rows if row["route"] == "AUTO_RESOLVE"),
        "retrieval_rows": retrieval_rows,
        "triage_rows": triage_rows,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "articles_indexed", "retrieval_top1_accuracy", "retrieval_above_answer_threshold_rate", "unsafe_auto_resolves",
        "complete_mcp_traces", "grounded_auto_resolves",
    )}, indent=2))
    print(f"Detailed report: {REPORT_PATH}")
    if retrieval_accuracy < 0.85 or unsafe_auto_resolves or not report["grounded_auto_resolves"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
