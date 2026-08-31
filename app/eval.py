import json
from collections import Counter
from pathlib import Path

from app.graph import build_workflow
from app.mcp_client import MCPClient
from app.models import Ticket
from app.providers import DeterministicProvider


def load_fixture(path: str):
    return json.loads(Path(__file__).resolve().parent.joinpath(path).read_text())


def run_golden_eval():
    tickets = load_fixture("fixtures/tickets.json")
    golden = [ticket for ticket in tickets if ticket.get("golden")]
    provider = DeterministicProvider()
    mcp_client = MCPClient()
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
        results.append({
            "ticket_id": ticket["ticket_id"],
            "expected_category": ticket["expected_category"],
            "expected_route": ticket["expected_route"],
            "actual_category": result.category,
            "actual_route": result.route,
            "urgency": result.urgency,
            "processing_ms": result.processing_ms,
            "rule_codes": result.rule_codes,
            "passed": (result.category == ticket["expected_category"] and result.route == ticket["expected_route"]),
        })

    return results


def build_eval_report() -> dict:
    rows = run_golden_eval()
    total = len(rows)
    expected_high_risk = [row for row in rows if row["expected_route"] == "ESCALATE"]
    route_counts = Counter(row["actual_route"] for row in rows)
    return {
        "run_label": "Deterministic Provider + real MCP stdio",
        "total": total,
        "category_accuracy": round(sum(row["actual_category"] == row["expected_category"] for row in rows) / total, 4),
        "route_accuracy": round(sum(row["actual_route"] == row["expected_route"] for row in rows) / total, 4),
        "high_risk_recall": round(sum(row["actual_route"] == "ESCALATE" for row in expected_high_risk) / max(1, len(expected_high_risk)), 4),
        "unsafe_auto_resolves": sum(row["expected_route"] == "ESCALATE" and row["actual_route"] == "AUTO_RESOLVE" for row in rows),
        "average_latency_ms": round(sum(row["processing_ms"] for row in rows) / total),
        "route_distribution": dict(route_counts),
        "rows": rows,
    }


if __name__ == "__main__":
    print(json.dumps(build_eval_report(), indent=2))
