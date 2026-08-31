from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, StateGraph

from app.config import KB_THRESHOLD
from app.guardrails import evaluate_guardrails
from app.models import Classification, TriageResult, TraceEvent


BLOCKING_RISK_CODES = {
    "HIGH_URGENCY", "CRITICAL_URGENCY", "ANGRY_CUSTOMER", "THREAT_DETECTED",
    "SECURITY_RISK", "PAYMENT_FAILURE", "REFUND_OR_LEGAL",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def add_trace(
    state: dict[str, Any],
    node: str,
    started: float,
    status: str,
    message: str,
    data: dict[str, Any],
) -> None:
    state["trace"].append(TraceEvent(
        node=node,
        timestamp=now_iso(),
        duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
        status=status,
        message=message,
        data=data,
    ))


def perceive(state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    ticket = state["ticket"]
    state["started_at"] = started
    state["correlation_id"] = str(uuid.uuid4())
    state["normalized_text"] = f"{ticket.subject}\n{ticket.body}".strip()
    add_trace(state, "perceive", started, "success", "Ticket normalized and correlation ID assigned.", {
        "ticket_id": ticket.ticket_id,
        "subject": ticket.subject,
        "correlation_id": state["correlation_id"],
    })
    return state


def classify(state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        classification = Classification.model_validate(
            state["provider"].classify(state["normalized_text"])
        ).model_dump()
        state["classification"] = classification
        add_trace(state, "classify", started, "success", "Structured classification validated.", classification)
    except Exception as exc:
        state["provider_error"] = type(exc).__name__
        state["classification"] = {
            "category": "technical",
            "urgency": "medium",
            "confidence": 0.0,
            "queue": "Human Triage",
        }
        add_trace(state, "classify", started, "error", "Provider failed; safe human routing activated.", {
            "error_type": type(exc).__name__,
            "fallback": "ESCALATE",
        })
    return state


def risk_guard(state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    classification = state["classification"]
    guard = evaluate_guardrails({
        "urgency": classification["urgency"],
        "content": state["normalized_text"],
    })
    if state.get("provider_error"):
        guard["rule_codes"].append("MODEL_ERROR")
        guard["blocking"] = True
    state["guardrails"] = guard
    add_trace(
        state,
        "risk_guard",
        started,
        "warning" if guard["blocking"] else "success",
        "Deterministic safety policy evaluated.",
        guard,
    )
    return state


def kb_search_mcp(state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    category = state["classification"]["category"]
    try:
        matches = state["mcp_client"].search(state["normalized_text"], category, top_k=3)
        state["kb_matches"] = matches
        state["mcp_connected"] = True
        add_trace(state, "kb_search_mcp", started, "success", "Knowledge base searched across the MCP stdio boundary.", {
            "tool_name": "search_knowledge_base",
            "transport": "stdio",
            "request": getattr(state["mcp_client"], "last_request", None),
            "matches": matches,
        })
    except Exception as exc:
        state["kb_matches"] = []
        state["mcp_connected"] = False
        state["mcp_error"] = type(exc).__name__
        add_trace(state, "kb_search_mcp", started, "error", "MCP lookup failed; auto-resolution disabled.", {
            "tool_name": "search_knowledge_base",
            "error_type": type(exc).__name__,
            "fallback": "ESCALATE",
        })
    return state


def decide(state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    rules = list(state["guardrails"]["rule_codes"])
    top_match = (state.get("kb_matches") or [None])[0]
    top_score = float(top_match.get("score", 0.0)) if top_match else 0.0

    if state.get("mcp_error"):
        rules.append("MCP_UNAVAILABLE")
        decision = "ESCALATE"
    elif "MODEL_ERROR" in rules:
        decision = "ESCALATE"
    elif any(code in BLOCKING_RISK_CODES for code in rules):
        decision = "ESCALATE"
    elif "MISSING_INFORMATION" in rules:
        decision = "CLARIFY"
    elif state["classification"]["confidence"] < 0.65:
        rules.append("LOW_CLASSIFICATION_CONFIDENCE")
        decision = "ESCALATE"
    elif top_score < KB_THRESHOLD:
        rules.append("LOW_KB_CONFIDENCE")
        decision = "ESCALATE"
    else:
        decision = "AUTO_RESOLVE"

    state["decision"] = decision
    state["rule_codes"] = list(dict.fromkeys(rules))
    state["kb_score"] = top_score
    add_trace(
        state,
        "decide",
        started,
        "warning" if decision != "AUTO_RESOLVE" else "success",
        "Route selected from safety rules and MCP evidence.",
        {
            "decision": decision,
            "rule_codes": state["rule_codes"],
            "kb_score": top_score,
            "threshold": KB_THRESHOLD,
        },
    )
    return state


def draft_resolution(state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    match = state["kb_matches"][0]
    state["draft"] = (
        f"Hi {state['ticket'].customer_id or 'there'},\n\n"
        f"{match['excerpt']}\n\nReference: {match['article_id']} — {match['title']}"
    )
    add_trace(state, "draft_resolution", started, "success", "Grounded response draft created.", {
        "article_id": match["article_id"],
        "citation": match["title"],
    })
    return state


def draft_clarification(state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    state["draft"] = "Please share the order number, invoice ID, or purchase email so the billing team can locate the transaction."
    add_trace(state, "draft_clarification", started, "warning", "A focused clarification question was prepared.", {
        "required_fact": "order, invoice, or purchase email",
    })
    return state


def create_escalation(state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    queue = state["classification"]["queue"]
    state["draft"] = f"Human review required in {queue}. Triggered controls: {', '.join(state['rule_codes']) or 'policy review'}."
    add_trace(state, "create_escalation", started, "warning", "Human-review handoff created.", {
        "queue": queue,
        "rule_codes": state["rule_codes"],
    })
    return state


def observe(state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    match = (state.get("kb_matches") or [None])[0]
    route = state["decision"]
    evidence = f"help article match {state['kb_score']:.0%} (minimum {KB_THRESHOLD:.0%})"
    if state["rule_codes"]:
        evidence += f"; controls: {', '.join(state['rule_codes'])}"
    add_trace(state, "observe", started, "success", "Final decision assembled from recorded evidence.", {
        "route": route,
        "evidence": evidence,
    })
    state["result"] = TriageResult(
        ticket_id=state["ticket"].ticket_id,
        route=route,
        category=state["classification"]["category"],
        urgency=state["classification"]["urgency"],
        confidence=state["classification"]["confidence"],
        queue=state["classification"]["queue"],
        correlation_id=state["correlation_id"],
        processing_ms=max(0, round((time.perf_counter() - state["started_at"]) * 1000)),
        mcp_connected=state.get("mcp_connected", False),
        kb_match=match,
        decision_summary=f"{route}: {evidence}.",
        rule_codes=state["rule_codes"],
        draft=state.get("draft"),
        trace=state["trace"],
    )
    return state


def build_workflow(provider: Any, mcp_client: Any):
    workflow = StateGraph(dict)
    for name, node in [
        ("perceive", perceive),
        ("classify", classify),
        ("risk_guard", risk_guard),
        ("kb_search_mcp", kb_search_mcp),
        ("decide", decide),
        ("draft_resolution", draft_resolution),
        ("draft_clarification", draft_clarification),
        ("create_escalation", create_escalation),
        ("observe", observe),
    ]:
        workflow.add_node(name, node)
    workflow.set_entry_point("perceive")
    workflow.add_edge("perceive", "classify")
    workflow.add_edge("classify", "risk_guard")
    workflow.add_edge("risk_guard", "kb_search_mcp")
    workflow.add_edge("kb_search_mcp", "decide")
    workflow.add_conditional_edges(
        "decide",
        lambda state: {
            "AUTO_RESOLVE": "draft_resolution",
            "CLARIFY": "draft_clarification",
        }.get(state.get("decision"), "create_escalation"),
        {
            "draft_resolution": "draft_resolution",
            "draft_clarification": "draft_clarification",
            "create_escalation": "create_escalation",
        },
    )
    workflow.add_edge("draft_resolution", "observe")
    workflow.add_edge("draft_clarification", "observe")
    workflow.add_edge("create_escalation", "observe")
    workflow.add_edge("observe", END)
    return workflow.compile()
