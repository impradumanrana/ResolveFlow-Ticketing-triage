from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Ticket(BaseModel):
    ticket_id: str
    customer_id: str | None = None
    subject: str
    body: str
    category: str | None = None
    urgency: str | None = None
    route: str | None = None
    confidence: float | None = None


class Classification(BaseModel):
    category: Literal["billing", "technical", "account"]
    urgency: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0)
    queue: str


class KBMatch(BaseModel):
    article_id: str
    title: str
    score: float = Field(ge=0.0, le=1.0)
    excerpt: str
    category: str


class TraceEvent(BaseModel):
    node: str
    timestamp: str
    duration_ms: int
    status: Literal["start", "success", "warning", "error"]
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class TriageResult(BaseModel):
    ticket_id: str
    route: Literal["AUTO_RESOLVE", "CLARIFY", "ESCALATE"]
    category: str
    urgency: str
    confidence: float = Field(ge=0.0, le=1.0)
    queue: str = "General Support"
    correlation_id: str = ""
    processing_ms: int = 0
    mcp_connected: bool = False
    kb_match: KBMatch | None = None
    decision_summary: str
    rule_codes: list[str] = Field(default_factory=list)
    draft: str | None = None
    trace: list[TraceEvent] = Field(default_factory=list)
