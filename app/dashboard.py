from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import textwrap
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

from app.cli import triage_ticket
from app.config import HAS_OPENAI_KEY, KB_THRESHOLD, OPENAI_EMBEDDING_MODEL, OPENAI_MODEL
from app.eval import build_eval_report
from app.knowledge_store import (
    add_articles,
    clear_articles,
    knowledge_stats,
    list_articles,
    prepare_demo_knowledge,
    replace_articles,
    restore_built_in_articles,
)
from app.models import Ticket
from app.mcp_client import MCPClient
from app.providers import DeterministicProvider, OpenAIProvider


APP_ROOT = Path(__file__).resolve().parent
DEMO_IDS = ["T-001", "T-003", "T-002", "T-004", "T-005", "T-020"]
SAMPLE_BATCHES = {
    "Guided demo · 6 tickets": DEMO_IDS,
    "Operations batch · all 36 tickets": None,
}
ROUTE_ORDER = ["AUTO_RESOLVE", "CLARIFY", "ESCALATE"]
ROUTE_LABELS = {
    "AUTO_RESOLVE": "Auto-resolved",
    "CLARIFY": "Needs clarification",
    "ESCALATE": "Human review",
}
TRACE_LABELS = {
    "perceive": "Read ticket",
    "classify": "Classify issue",
    "risk_guard": "Check safety and urgency",
    "kb_search_mcp": "Search support knowledge base",
    "decide": "Choose route",
    "draft_resolution": "Prepare answer draft",
    "validate_grounding": "Validate citations and grounding",
    "draft_clarification": "Prepare clarification question",
    "create_escalation": "Prepare human handoff",
    "observe": "Assemble final result",
}


def load_tickets() -> list[dict[str, Any]]:
    return json.loads(APP_ROOT.joinpath("fixtures/tickets.json").read_text())


@st.cache_data(ttl=300, show_spinner=False)
def openai_is_active() -> bool:
    if os.getenv("RESOLVEFLOW_TEST_MODE") == "1":
        return True
    return OpenAIProvider().health_check()


def load_knowledge_articles() -> list[dict[str, Any]]:
    return list_articles()


def save_custom_articles(articles: list[dict[str, Any]]) -> None:
    clear_articles("user")
    if articles:
        add_articles(articles)


def run_knowledge_change(action: Any) -> Any | None:
    """Show indexing/API failures as useful UI feedback instead of a Streamlit crash."""
    try:
        return action()
    except Exception as exc:
        st.error(
            "Knowledge indexing could not finish. Check the OpenAI connection and try again. "
            f"Technical detail: {type(exc).__name__}."
        )
        return None


def parse_knowledge_csv(uploaded_file: Any, existing_ids: set[str]) -> list[dict[str, Any]]:
    text = uploaded_file.getvalue().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    required = {"article_id", "title", "category", "excerpt"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("CSV must include article_id, title, category, and excerpt columns.")
    new_articles = []
    seen = set(existing_ids)
    for index, row in enumerate(reader, start=1):
        article_id = (row.get("article_id") or "").strip()
        title = (row.get("title") or "").strip()
        category = (row.get("category") or "").strip().lower()
        excerpt = (row.get("excerpt") or "").strip()
        if not all([article_id, title, category, excerpt]):
            raise ValueError(f"Article row {index} has an empty required field.")
        if category not in {"billing", "technical", "account"}:
            raise ValueError(f"Article row {index} has invalid category '{category}'.")
        if article_id in seen:
            raise ValueError(f"Article ID {article_id} already exists or appears twice.")
        seen.add(article_id)
        keywords = [item.strip() for item in (row.get("keywords") or "").split("|") if item.strip()]
        new_articles.append({
            "article_id": article_id,
            "title": title,
            "category": category,
            "excerpt": excerpt,
            "keywords": keywords,
        })
    if not new_articles:
        raise ValueError("The CSV contains no knowledge articles.")
    if len(new_articles) > 100:
        raise ValueError("Import up to 100 knowledge articles at a time.")
    return new_articles


def parse_knowledge_documents(uploaded_files: list[Any], category: str, existing_ids: set[str]) -> list[dict[str, Any]]:
    if len(uploaded_files) > 10:
        raise ValueError("Upload up to 10 knowledge files at a time.")
    articles: list[dict[str, Any]] = []
    seen = set(existing_ids)
    for uploaded in uploaded_files:
        filename = Path(uploaded.name).name
        payload = uploaded.getvalue()
        if len(payload) > 10 * 1024 * 1024:
            raise ValueError(f"{filename} is larger than the 10 MB per-file limit.")
        suffix = Path(filename).suffix.lower()
        if suffix == ".pdf":
            from pypdf import PdfReader
            try:
                reader = PdfReader(io.BytesIO(payload))
                text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
            except Exception as exc:
                raise ValueError(f"Could not read {filename}: {type(exc).__name__}.") from exc
        elif suffix in {".md", ".txt"}:
            text = payload.decode("utf-8-sig")
        else:
            raise ValueError(f"Unsupported knowledge file: {filename}")
        text = re.sub(r"[ \t]+", " ", text).strip()
        if not text:
            raise ValueError(f"No readable text was found in {filename}.")

        blocks = [re.sub(r"\s+", " ", block).strip(" #\t") for block in re.split(r"\n\s*\n|(?=^#{1,3}\s)", text, flags=re.MULTILINE)]
        bounded_blocks = [
            part
            for block in blocks if block
            for part in textwrap.wrap(block, width=900, break_long_words=False, break_on_hyphens=False)
        ]
        chunks: list[str] = []
        pending = ""
        for block in bounded_blocks:
            if len(pending) + len(block) + 2 <= 900:
                pending = f"{pending}\n\n{block}".strip()
            else:
                if pending:
                    chunks.append(pending)
                pending = block
        if pending:
            chunks.append(pending)

        slug = re.sub(r"[^A-Z0-9]+", "-", Path(filename).stem.upper()).strip("-")[:28] or "DOCUMENT"
        for index, chunk in enumerate(chunks, start=1):
            article_id = f"DOC-{slug}-{index:03d}"
            if article_id in seen:
                raise ValueError(f"Article ID {article_id} already exists. Rename the file or replace the collection.")
            seen.add(article_id)
            words = [word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", chunk)]
            common = [word for word, _ in Counter(words).most_common(8)]
            articles.append({
                "article_id": article_id,
                "title": f"{Path(filename).stem} · section {index}",
                "category": category,
                "excerpt": chunk,
                "keywords": common,
            })
    if not articles:
        raise ValueError("The selected files produced no knowledge sections.")
    if len(articles) > 100:
        raise ValueError("Import up to 100 extracted sections at a time. Split larger knowledge sets into batches.")
    return articles


def setup_state() -> None:
    defaults = {
        "results": {},
        "ticket_inputs": {},
        "audit_log": [],
        "selected_ticket": DEMO_IDS[0],
        "eval_report": None,
        "kb_notice": None,
        "kb_search_results": None,
        "kb_action": "Add article",
        "kb_csv_upload_version": 0,
        "kb_document_upload_version": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root { --navy:#0b1739; --indigo:#4f46e5; --ink:#172033; --muted:#596579; --paper:#f7f8fc; }
        .stApp { background:linear-gradient(135deg,#fbfaf7 0%,#f7f8fc 58%,#eef1fb 100%); color:var(--ink); }
        [data-testid="stMain"] { color:var(--ink); }
        [data-testid="stMain"] h1, [data-testid="stMain"] h2, [data-testid="stMain"] h3, [data-testid="stMain"] h4 { color:#10182b !important; }
        [data-testid="stMain"] [data-testid="stWidgetLabel"] p,
        [data-testid="stMain"] label p,
        [data-testid="stMain"] summary span { color:#344054 !important; font-weight:600; }
        [data-testid="stMain"] input,
        [data-testid="stMain"] textarea { color:#172033 !important; background:#fff !important; caret-color:#4f46e5 !important; }
        [data-testid="stMain"] [data-testid="stTextInput"] [data-baseweb="input"],
        [data-testid="stMain"] [data-testid="stTextArea"] [data-baseweb="textarea"] { background:#fff !important; border:1px solid #98a2b3 !important; border-radius:8px !important; box-shadow:0 1px 2px rgba(16,24,40,.06) !important; }
        [data-testid="stMain"] [data-testid="stTextInput"] [data-baseweb="input"]:focus-within,
        [data-testid="stMain"] [data-testid="stTextArea"] [data-baseweb="textarea"]:focus-within { border-color:#4f46e5 !important; box-shadow:0 0 0 3px rgba(79,70,229,.16) !important; }
        [data-testid="stMain"] input::placeholder,
        [data-testid="stMain"] textarea::placeholder { color:#7b8497 !important; opacity:1; }
        [data-testid="stMain"] [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        [data-testid="stMain"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div { background:#fff !important; color:#172033 !important; border:1px solid #98a2b3 !important; border-radius:8px !important; min-height:42px !important; box-shadow:0 1px 2px rgba(16,24,40,.06) !important; cursor:pointer !important; }
        [data-testid="stMain"] [data-testid="stSelectbox"] [data-baseweb="select"] > div:hover,
        [data-testid="stMain"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div:hover { border-color:#667085 !important; background:#fdfdff !important; }
        [data-testid="stMain"] [data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within,
        [data-testid="stMain"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div:focus-within { border-color:#4f46e5 !important; box-shadow:0 0 0 3px rgba(79,70,229,.16) !important; }
        [data-testid="stMain"] [data-baseweb="select"] span { color:#172033 !important; }
        [data-testid="stMain"] [data-baseweb="select"] svg { color:#475467 !important; fill:#475467 !important; width:18px !important; height:18px !important; }
        [data-baseweb="popover"] [role="listbox"] { background:#fff !important; border:1px solid #98a2b3 !important; border-radius:8px !important; box-shadow:0 12px 28px rgba(16,24,40,.16) !important; }
        [data-baseweb="popover"] [role="option"] { color:#172033 !important; background:#fff !important; }
        [data-baseweb="popover"] [role="option"]:hover,
        [data-baseweb="popover"] [aria-selected="true"] { background:#eef2ff !important; color:#312e81 !important; }
        [data-testid="stMain"] [data-testid="stFileUploaderDropzone"] { background:#fff !important; border:1.5px dashed #98a2b3 !important; border-radius:10px !important; color:#344054 !important; }
        [data-testid="stMain"] [data-testid="stFileUploaderDropzone"]:hover { border-color:#4f46e5 !important; background:#f7f7ff !important; }
        [data-testid="stMain"] [data-testid="stFileUploaderDropzone"] small,
        [data-testid="stMain"] [data-testid="stFileUploaderDropzone"] span { color:#596579 !important; opacity:1 !important; }
        [data-testid="stMain"] [data-baseweb="tab-list"] { gap:6px; border-bottom:1px solid #cfd5e3 !important; }
        [data-testid="stMain"] [data-baseweb="tab"] { color:#475467 !important; background:#f5f7fb !important; border:1px solid #d0d5dd !important; border-bottom:0 !important; border-radius:9px 9px 0 0 !important; padding:10px 16px !important; }
        [data-testid="stMain"] [data-baseweb="tab"][aria-selected="true"] { color:#3730a3 !important; background:#fff !important; border-color:#818cf8 !important; font-weight:750 !important; box-shadow:inset 0 3px 0 #4f46e5 !important; }
        [data-testid="stMain"] [data-testid="stRadio"] label { border-radius:8px; padding:4px 7px; }
        [data-testid="stMain"] [data-testid="stRadio"] label:hover { background:#f1f4ff !important; }
        [data-testid="stMain"] [data-testid="stRadio"] [data-baseweb="radio"] > div:first-child,
        [data-testid="stMain"] [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child { border-color:#667085 !important; }
        [data-testid="stMain"] [data-testid="stExpander"] details { background:#fff !important; border:1px solid #cfd5e3 !important; border-radius:10px !important; }
        [data-testid="stMain"] [data-testid="stExpander"] summary:hover { background:#f7f8fc !important; }
        [data-testid="stMain"] textarea:disabled,
        [data-testid="stMain"] input:disabled { color:#344054 !important; -webkit-text-fill-color:#344054 !important; background:#f2f4f7 !important; opacity:1 !important; }
        [data-testid="stMetric"] { background:#fff; border:1px solid #dfe3ee; padding:18px; border-radius:16px; box-shadow:0 8px 24px rgba(17,24,39,.05); min-height:148px; }
        [data-testid="stMetricLabel"] p { color:#536178 !important; font-weight:700 !important; }
        [data-testid="stMetricValue"] { color:#111b35 !important; font-weight:750 !important; }
        [data-testid="stMetricDelta"] div { color:#087443 !important; font-weight:650 !important; }
        [data-testid="stMetricDelta"] svg { fill:#087443 !important; }
        [data-testid="stMain"] button[kind="secondary"] { background:#fff !important; color:#26334d !important; border:1px solid #bfc7d8 !important; }
        [data-testid="stMain"] button[kind="secondary"] p { color:#26334d !important; font-weight:700 !important; }
        [data-testid="stMain"] button[kind="primary"] { background:#4f46e5 !important; border-color:#4f46e5 !important; }
        [data-testid="stMain"] button[kind="primary"] p { color:#fff !important; font-weight:750 !important; }
        [data-testid="stMain"] button[kind="secondary"]:hover { background:#f8f9fc !important; border-color:#667085 !important; }
        [data-testid="stMain"] button[kind="primary"]:hover { background:#4338ca !important; border-color:#4338ca !important; }
        [data-testid="stMain"] button:focus-visible { outline:3px solid rgba(79,70,229,.28) !important; outline-offset:2px !important; }
        [data-testid="stMain"] button:disabled { opacity:.58 !important; cursor:not-allowed !important; }
        [data-testid="stMain"] [data-testid="baseButton-secondary"] { background:#fff !important; color:#26334d !important; border:1px solid #bfc7d8 !important; }
        [data-testid="stMain"] [data-testid="baseButton-secondary"] p { color:#26334d !important; font-weight:700 !important; }
        [data-testid="stMain"] [data-testid="baseButton-primary"] { background:#4f46e5 !important; color:#fff !important; border-color:#4f46e5 !important; }
        [data-testid="stMain"] [data-testid="baseButton-primary"] p { color:#fff !important; font-weight:750 !important; }
        [data-testid="stMain"] [data-testid="stAlert"] p { color:#25324b !important; }
        .hero { padding:26px 28px; background:linear-gradient(115deg,#101d43,#222969 65%,#5b5bd6); border-radius:22px; color:white; margin-bottom:22px; box-shadow:0 18px 48px rgba(18,30,72,.18); }
        .hero h1 { margin:0; font-size:2.25rem; letter-spacing:-.04em; color:#fff !important; }
        .hero p { margin:.45rem 0 0; color:#e6eaff !important; font-size:1.03rem; }
        .eyebrow { text-transform:uppercase; letter-spacing:.13em; font-size:.72rem; font-weight:700; color:#b9c4ff; }
        .status-pill { display:inline-flex; align-items:center; gap:7px; border-radius:999px; padding:6px 10px; background:#ecfdf3; color:#087443; font-weight:700; font-size:.78rem; }
        .status-pill:before { content:""; width:7px; height:7px; border-radius:50%; background:#19a974; }
        .status-pill.empty { background:#fff7ed; color:#9a3412; }
        .status-pill.empty:before { background:#f97316; }
        .ticket-card { background:#fff; border:1px solid #e7e9f3; border-radius:16px; padding:18px 20px; margin:8px 0 14px; }
        .route-auto { color:#087443; } .route-clarify { color:#9a6700; } .route-escalate { color:#bd1e2d; }
        .compact-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(145px,1fr)); gap:10px; margin:8px 0 18px; }
        .compact-fact { background:#fff; border:1px solid #dfe3ee; border-radius:12px; padding:11px 13px; min-height:72px; }
        .compact-fact .label { color:#667085; font-size:.72rem; line-height:1.2; font-weight:750; text-transform:uppercase; letter-spacing:.055em; }
        .compact-fact .value { color:#172033; font-size:1rem; line-height:1.3; font-weight:750; margin-top:6px; overflow-wrap:anywhere; }
        .compact-grid.mcp-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
        .quick-start { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:4px 0 20px; }
        .quick-start > div { background:#fff; border:1px solid #dfe3ee; border-radius:14px; padding:14px 15px; color:#344054; }
        .quick-start span { display:inline-grid; place-items:center; width:25px; height:25px; margin-right:7px; border-radius:8px; background:#eef2ff; color:#4338ca; font-weight:800; }
        .quick-start b { color:#172033; }
        .route-legend { background:#f1f4ff; border:1px solid #d8def7; color:#344054; border-radius:12px; padding:11px 14px; margin:4px 0 18px; font-size:.88rem; }
        .how-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:12px 0 22px; }
        .how-card { background:#fff; border:1px solid #dfe3ee; border-radius:14px; padding:15px 16px; color:#475467; min-height:118px; }
        .how-card .step { color:#4f46e5; font-size:.72rem; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }
        .how-card b { display:block; color:#172033; font-size:1rem; margin:5px 0; }
        .how-callout { background:#eef2ff; border:1px solid #cfd6fb; border-left:4px solid #4f46e5; border-radius:12px; color:#344054; padding:14px 16px; margin:10px 0 20px; }
        .flow { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:14px 0 22px; }
        .flow span { background:#fff; border:1px solid #dfe3f1; border-radius:10px; padding:9px 12px; font-size:.82rem; font-weight:700; }
        .flow b { color:#7a8295; }
        div[data-testid="stDataFrame"] { border:1px solid #e7e9f3; border-radius:14px; overflow:hidden; }
        [data-testid="stSidebar"] { background:#0b1739; }
        [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p, [data-testid="stSidebar"] label, [data-testid="stSidebar"] span { color:#eef2ff !important; }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color:#c5d0ea !important; opacity:1 !important; }
        [data-testid="stSidebar"] span.status-pill { background:#d8f8e8 !important; color:#06633f !important; border:1px solid #91dfb8 !important; }
        [data-testid="stSidebar"] span.status-pill:before { background:#0c9b68 !important; }
        [data-testid="stSidebar"] span.status-pill.empty { background:#fff1df !important; color:#8a3412 !important; border-color:#fdba74 !important; }
        [data-testid="stSidebar"] span.status-pill.empty:before { background:#ea580c !important; }
        [data-testid="stSidebar"] [data-testid="stExpander"],
        [data-testid="stSidebar"] [data-testid="stExpander"] details,
        [data-testid="stSidebar"] [data-testid="stExpander"] summary,
        [data-testid="stSidebar"] [data-testid="stExpanderDetails"] { background:#13254f !important; border-color:#31456f !important; }
        [data-testid="stSidebar"] [data-testid="stExpander"] summary:hover { background:#1a315f !important; }
        [data-testid="stSidebar"] [data-testid="stExpander"] svg { fill:#dbe4ff !important; color:#dbe4ff !important; }
        [data-testid="stSidebar"] [data-testid="stExpander"] p,
        [data-testid="stSidebar"] [data-testid="stExpander"] span { color:#eaf0ff !important; }
        @media (max-width:900px) { .quick-start, .how-grid, .compact-grid, .compact-grid.mcp-grid { grid-template-columns:1fr; } [data-testid="stMetric"] { min-height:auto; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def ticket_from_fixture(raw: dict[str, Any]) -> Ticket:
    return Ticket(**{key: value for key, value in raw.items() if key in Ticket.model_fields})


def run_ticket(raw: dict[str, Any], mcp_client: MCPClient | None = None) -> dict[str, Any]:
    provider = DeterministicProvider() if os.getenv("RESOLVEFLOW_TEST_MODE") == "1" else None
    return triage_ticket(ticket_from_fixture(raw), provider=provider, mcp_client=mcp_client).model_dump()


def run_ticket_batch(raw_tickets: list[dict[str, Any]], label: str, mcp_client: MCPClient | None = None) -> None:
    progress = st.progress(0, text="Starting the inspectable triage graph…")
    for index, raw in enumerate(raw_tickets):
        ticket_id = raw["ticket_id"]
        progress.progress(index / len(raw_tickets), text=f"Processing {ticket_id} · {index + 1} of {len(raw_tickets)}")
        st.session_state.results[ticket_id] = run_ticket(raw, mcp_client=mcp_client)
        st.session_state.ticket_inputs[ticket_id] = raw
    progress.empty()
    st.toast(f"{label} is ready. Choose a ticket below to inspect the decision.", icon="✅")


def run_sample_batch(batch_name: str) -> None:
    fixtures = load_tickets()
    ticket_ids = SAMPLE_BATCHES[batch_name]
    selected = fixtures if ticket_ids is None else [row for ticket_id in ticket_ids for row in fixtures if row["ticket_id"] == ticket_id]
    demo_client = MCPClient(kb_db_path=prepare_demo_knowledge())
    run_ticket_batch(selected, batch_name, mcp_client=demo_client)


def parse_uploaded_batch(uploaded_file: Any) -> list[dict[str, Any]]:
    text = uploaded_file.getvalue().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"subject", "body"}.issubset(reader.fieldnames):
        raise ValueError("CSV must include subject and body columns.")
    rows = []
    seen_ids: set[str] = set()
    for index, row in enumerate(reader, start=1):
        if not (row.get("subject") or "").strip() or not (row.get("body") or "").strip():
            raise ValueError(f"Row {index} needs both subject and body.")
        ticket_id = (row.get("ticket_id") or f"IMPORT-{index:03d}").strip()
        if ticket_id in seen_ids:
            raise ValueError(f"Ticket ID {ticket_id} appears more than once.")
        seen_ids.add(ticket_id)
        rows.append({
            "ticket_id": ticket_id,
            "customer_id": (row.get("customer_id") or "").strip() or None,
            "subject": row["subject"].strip(),
            "body": row["body"].strip(),
            "_source": "CSV import",
        })
    if not rows:
        raise ValueError("The CSV contains no ticket rows.")
    if len(rows) > 50:
        raise ValueError("Import up to 50 tickets at a time for this demo.")
    return rows


def reset_demo() -> None:
    st.session_state.results = {}
    st.session_state.ticket_inputs = {}
    st.session_state.audit_log = []
    st.session_state.selected_ticket = DEMO_IDS[0]


def result_rows(results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    fixtures = {item["ticket_id"]: item for item in load_tickets()}
    rows = []
    for ticket_id, result in results.items():
        raw = st.session_state.ticket_inputs.get(ticket_id, fixtures.get(ticket_id, {}))
        reviewed = any(event.get("ticket_id") == ticket_id for event in st.session_state.audit_log)
        status = "Reviewed" if reviewed else {
            "AUTO_RESOLVE": "Draft ready",
            "CLARIFY": "Waiting for details",
            "ESCALATE": "Needs human review",
        }.get(result["route"], "Open")
        source = raw.get("_source") or ("Manual" if ticket_id.startswith("CUSTOM-") else "Sample data")
        rows.append({
            "Ticket": ticket_id,
            "Subject": raw.get("subject", "Custom ticket"),
            "Source": source,
            "Status": status,
            "Category": result["category"].title(),
            "Urgency": result["urgency"].title(),
            "Response target": response_target(result["urgency"]),
            "Route": ROUTE_LABELS.get(result["route"], result["route"]),
            "Confidence": f"{result['confidence']:.0%}",
            "Help article match": f"{result['kb_match']['score']:.0%}" if result.get("kb_match") else "—",
            "Queue": result.get("queue", "General Support"),
        })
    return rows


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return buffer.getvalue().encode()


def compact_facts(items: list[tuple[str, Any]], extra_class: str = "") -> None:
    cards = "".join(
        f'<div class="compact-fact"><div class="label">{html.escape(str(label))}</div><div class="value">{html.escape(str(value))}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="compact-grid {extra_class}">{cards}</div>', unsafe_allow_html=True)


def response_target(urgency: str) -> str:
    return {"critical": "15 minutes", "high": "1 hour", "medium": "8 hours", "low": "24 hours"}.get(urgency, "24 hours")


def render_header() -> None:
    st.markdown(
        """
        <section class="hero">
          <div class="eyebrow">Support operations workspace</div>
          <h1>ResolveFlow AI</h1>
          <p>Sort incoming tickets, catch urgent risks, and prepare grounded answers in seconds.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_kpis(results: dict[str, dict[str, Any]]) -> None:
    total = len(results)
    auto = sum(item["route"] == "AUTO_RESOLVE" for item in results.values())
    human = sum(item["route"] == "ESCALATE" for item in results.values())
    sla = sum(item["urgency"] in {"high", "critical"} for item in results.values())
    values = [
        ("Tickets processed", total, "in this workspace"),
        ("Answer drafts", auto, "ready to review"),
        ("Needs a person", human, "safely held back"),
        ("Urgent tickets", sla, "high or critical"),
        ("Est. time saved", f"{auto * 4} min", "4 min per draft"),
    ]
    for col, (label, value, note) in zip(st.columns(5), values):
        col.metric(label, value, note)


def trace_timeline(result: dict[str, Any]) -> None:
    st.subheader("Agent trace")
    st.caption("Recorded state evidence only — no hidden chain-of-thought.")
    for index, event in enumerate(result.get("trace", []), start=1):
        icon = "✓" if event["status"] == "success" else "!"
        label = TRACE_LABELS.get(event["node"], event["node"].replace("_", " ").title())
        with st.expander(f"{index:02d}  {icon}  {label} · {event['duration_ms']} ms"):
            st.write(event["message"])
            st.json(event["data"])


def mcp_evidence(result: dict[str, Any]) -> None:
    event = next((item for item in result.get("trace", []) if item["node"] == "kb_search_mcp"), None)
    match = result.get("kb_match")
    st.subheader("Support knowledge base evidence")
    compact_facts([
        ("Server status", "Connected" if result.get("mcp_connected") else "Offline"),
        ("Search action", "Qdrant semantic + keyword rerank"),
        ("Article match", f"{match['score']:.0%}" if match else "—"),
    ], "mcp-grid")
    st.caption("MCP uses stdio (standard input/output) here—a correct local transport that keeps the knowledge-base server in a separate process.")
    if event:
        st.json({"transport": event["data"].get("transport"), "request": event["data"].get("request"), "top_match": match, "duration_ms": event["duration_ms"]})


def detail_panel(ticket_id: str, result: dict[str, Any]) -> None:
    fixtures = {item["ticket_id"]: item for item in load_tickets()}
    raw = st.session_state.ticket_inputs.get(ticket_id, fixtures.get(ticket_id, {"subject": "Custom ticket", "body": ""}))
    route_class = {"AUTO_RESOLVE": "route-auto", "CLARIFY": "route-clarify", "ESCALATE": "route-escalate"}[result["route"]]
    st.markdown(f"### {raw['subject']}")
    st.markdown(f'<div class="ticket-card"><b>{ticket_id}</b><br><br>{raw.get("body", "")}</div>', unsafe_allow_html=True)
    compact_facts([
        ("Route", ROUTE_LABELS[result["route"]]),
        ("Category", result["category"].title()),
        ("Urgency", result["urgency"].title()),
        ("Response target", response_target(result["urgency"])),
        ("Assigned team", result.get("queue", "General Support")),
    ])
    st.markdown(f'<p class="{route_class}"><b>{result["decision_summary"]}</b></p>', unsafe_allow_html=True)
    if result.get("rule_codes"):
        st.caption("Controls triggered · " + " · ".join(result["rule_codes"]))

    left, right = st.columns([1.05, 1])
    with left:
        st.subheader("Recommended action")
        st.text_area("Draft / handoff note", result.get("draft") or "", height=170, disabled=True)
        if result.get("kb_match"):
            match = result["kb_match"]
            st.info(f"Matched help article: {match['article_id']} · {match['title']} · {match['score']:.0%} confidence")
        if result.get("grounding_validated"):
            citations = ", ".join(result.get("citations") or [])
            st.success(f"Grounding verified · citations: {citations}")
        elif result.get("route") == "AUTO_RESOLVE":
            st.warning("This draft has not passed grounding validation.")
        mcp_evidence(result)
    with right:
        trace_timeline(result)

    st.divider()
    st.subheader("Review or adjust this decision")
    st.caption("Nothing is sent to the customer. Your review is recorded in the session audit log.")
    with st.form(f"review-{ticket_id}"):
        r1, r2 = st.columns(2)
        override = r1.selectbox("Final route", ROUTE_ORDER, index=ROUTE_ORDER.index(result["route"]))
        queue = r2.selectbox("Send to team", ["Tier-1 Technical", "Billing Review", "Account Recovery", "Security Ops", "Human Triage"])
        note = st.text_input("Reviewer note", placeholder="Add a concise audit note")
        if st.form_submit_button("Record review decision", type="primary"):
            previous = result["route"]
            result["route"] = override
            result["queue"] = queue
            st.session_state.audit_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "ticket_id": ticket_id,
                "action": "APPROVED" if previous == override else "ROUTE_OVERRIDE",
                "previous_route": previous,
                "new_route": override,
                "queue": queue,
                "note": note,
            })
            st.success("Review decision recorded in the session audit log.")
    st.download_button("Download this trace (JSON)", json.dumps(result.get("trace", []), indent=2), file_name=f"{ticket_id.lower()}-trace.json", mime="application/json")


def operations_view() -> None:
    results = st.session_state.results
    st.markdown("### Start with tickets")
    sample_tab, manual_tab, import_tab = st.tabs([
        "Load sample batch",
        "Add one ticket manually",
        "Import ticket CSV",
    ])

    with sample_tab:
        action_a, action_b, action_c, action_d = st.columns([1.55, 1.2, 1, 1.8])
        batch_name = action_a.selectbox("Sample batch", list(SAMPLE_BATCHES), label_visibility="collapsed")
        if action_b.button("Load sample batch", type="primary", width="stretch"):
            run_sample_batch(batch_name)
            results = st.session_state.results
        if action_c.button("Clear all", width="stretch"):
            reset_demo()
            results = {}
        knowledge_label = "Protected demo knowledge · 15 articles"
        status_class = "status-pill"
        action_d.markdown(f'<span class="{status_class}">{knowledge_label}</span>', unsafe_allow_html=True)

    with manual_tab:
        st.caption("Paste a single customer request and route it immediately.")
        with st.form("single-ticket"):
            subject = st.text_input("Subject", placeholder="Short issue summary")
            body = st.text_area("Customer message", placeholder="Paste the incoming request", height=120)
            submitted = st.form_submit_button("Classify and route ticket", type="primary")
            if submitted:
                if not subject.strip() or not body.strip():
                    st.error("Add both a subject and customer message.")
                else:
                    custom_id = f"CUSTOM-{len(results) + 1:03d}"
                    custom = {"ticket_id": custom_id, "subject": subject, "body": body, "customer_id": None, "_source": "Manual"}
                    with st.spinner("Perceiving, checking risk, searching MCP, and routing…"):
                        st.session_state.results[custom_id] = run_ticket(custom)
                    st.session_state.ticket_inputs[custom_id] = custom
                    st.session_state.selected_ticket = custom_id
                    st.success(f"{custom_id} triaged safely.")
                    results = st.session_state.results

    with import_tab:
        st.write("Download a file, replace the examples with real support requests, then upload it here. Up to 50 tickets are processed through the same safety and support-knowledge workflow.")
        st.caption("Required: subject and body · Optional: ticket_id and customer_id · Do not include passwords, payment-card numbers, or other sensitive secrets.")
        dl_a, dl_b = st.columns(2)
        dl_a.download_button(
            "Empty CSV template",
            "ticket_id,customer_id,subject,body\n",
            "resolveflow-empty-template.csv",
            "text/csv",
        )
        dl_b.download_button(
            "Sample tickets (45)",
            APP_ROOT.joinpath("fixtures/realistic_ticket_batch_45.csv").read_bytes(),
            "resolveflow-realistic-45-tickets.csv",
            "text/csv",
        )
        uploaded = st.file_uploader("Choose a CSV file", type=["csv"])
        if uploaded is not None:
            try:
                imported = parse_uploaded_batch(uploaded)
                st.success(f"{len(imported)} valid tickets found.")
                st.dataframe(imported[:5], width="stretch", hide_index=True)
                if st.button("Process imported tickets", type="primary"):
                    run_ticket_batch(imported, f"Imported batch · {len(imported)} tickets")
                    results = st.session_state.results
            except (UnicodeDecodeError, ValueError) as exc:
                st.error(str(exc))

    st.markdown(
        """
        <div class="quick-start">
          <div><span>1</span><b>Load tickets</b><br>Use the sample set or add one manually.</div>
          <div><span>2</span><b>Review the route</b><br>See urgency, safety controls, and matched help articles.</div>
          <div><span>3</span><b>Take action</b><br>Approve, reroute, or download the trace.</div>
        </div>
        <div class="route-legend"><b>Route guide:</b> Answer draft = strong help article match · Clarification = customer detail needed · Human review = risk or uncertainty. No customer message is sent automatically.</div>
        """,
        unsafe_allow_html=True,
    )
    render_kpis(results)
    st.divider()

    st.subheader("Ticket queue")
    if not results:
        st.info("Choose one of the three options above to load sample tickets, paste one request, or import a CSV batch.")
        return

    rows = result_rows(results)
    f1, f2, f3, f4, f5 = st.columns([2, 1, 1, 1, 1.2])
    search = f1.text_input("Search queue", placeholder="Ticket ID, subject, or queue")
    route_filter = f2.selectbox("Route", ["All"] + list(ROUTE_LABELS.values()))
    category_filter = f3.selectbox("Category", ["All", "Billing", "Technical", "Account"])
    urgency_filter = f4.selectbox("Urgency", ["All", "Low", "Medium", "High", "Critical"])
    status_filter = f5.selectbox("Status", ["All", "Draft ready", "Waiting for details", "Needs human review", "Reviewed"])
    filtered = [
        row for row in rows
        if (not search or search.lower() in " ".join(str(value) for value in row.values()).lower())
        and (route_filter == "All" or row["Route"] == route_filter)
        and (category_filter == "All" or row["Category"] == category_filter)
        and (urgency_filter == "All" or row["Urgency"] == urgency_filter)
        and (status_filter == "All" or row["Status"] == status_filter)
    ]
    sort_left, sort_right = st.columns([4, 1.2])
    sort_left.caption(f"Showing {len(filtered)} of {len(rows)} processed tickets")
    sort_by = sort_right.selectbox("Sort queue", ["Priority first", "Ticket ID", "Highest confidence"])
    if sort_by == "Priority first":
        priority = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        filtered.sort(key=lambda row: (priority.get(row["Urgency"], 4), row["Ticket"]))
    elif sort_by == "Ticket ID":
        filtered.sort(key=lambda row: row["Ticket"])
    else:
        filtered.sort(key=lambda row: float(row["Confidence"].rstrip("%")), reverse=True)
    if filtered:
        st.dataframe(filtered, width="stretch", hide_index=True)
        st.download_button("Download filtered results (CSV)", csv_bytes(filtered), "resolveflow-results.csv", "text/csv")
    else:
        st.warning("No tickets match these filters.")

    available_ids = [row["Ticket"] for row in filtered] or list(results)
    default_index = available_ids.index(st.session_state.selected_ticket) if st.session_state.selected_ticket in available_ids else 0
    selected = st.selectbox("Choose a ticket to inspect", available_ids, index=default_index)
    st.session_state.selected_ticket = selected
    detail_panel(selected, results[selected])
    if st.session_state.audit_log:
        st.subheader("Session audit log")
        st.dataframe(st.session_state.audit_log, width="stretch", hide_index=True)


def evaluation_view() -> None:
    st.subheader("Quality and readiness check")
    st.write("Run a live evaluation of the configured OpenAI model, safety routing, hybrid knowledge retrieval, grounded answers, and MCP trace evidence.")
    st.info(f"Active classifier: OpenAI `{OPENAI_MODEL}` · protected 15-article evaluation corpus · 15 labelled tickets + 9 paraphrase searches · Nothing is sent to customers.")
    if st.button("Run live OpenAI quality check", type="primary"):
        with st.spinner("Running the current OpenAI model and real MCP searches. This can take about a minute…"):
            st.session_state.eval_report = build_eval_report(
                provider=OpenAIProvider(),
                run_label=f"OpenAI {OPENAI_MODEL} + hybrid knowledge + real MCP stdio",
                mcp_client=MCPClient(kb_db_path=prepare_demo_knowledge()),
            )
    report = st.session_state.eval_report
    if not report:
        st.info("Run the check to generate fresh measured results. Metrics are not precomputed or fabricated.")
        return
    st.caption(report["run_label"])
    checks = [
        ("Classification", report["category_accuracy"] >= 0.85),
        ("Routing", report["route_accuracy"] >= 0.85),
        ("Safety recall", report["high_risk_recall"] == 1.0),
        ("Unsafe automation", report["unsafe_auto_resolves"] == 0),
        ("Knowledge retrieval", report["retrieval_top1_accuracy"] >= 0.9),
        ("Grounding", report["grounded_draft_rate"] == 1.0),
        ("MCP evidence", report["mcp_trace_completeness"] == 1.0),
    ]
    passed_checks = sum(passed for _, passed in checks)
    if passed_checks == len(checks):
        st.success(f"Ready for demonstration · all {passed_checks} quality gates passed")
    else:
        st.warning(f"Review recommended · {passed_checks} of {len(checks)} quality gates passed")

    first_metrics = st.columns(4)
    for column, (label, value) in zip(first_metrics, [
        ("Category accuracy", f"{report['category_accuracy']:.0%}"),
        ("Route accuracy", f"{report['route_accuracy']:.0%}"),
        ("High-risk recall", f"{report['high_risk_recall']:.0%}"),
        ("Unsafe auto-resolves", report["unsafe_auto_resolves"]),
    ]):
        column.metric(label, value)
    second_metrics = st.columns(4)
    for column, (label, value) in zip(second_metrics, [
        ("Knowledge top-1", f"{report['retrieval_top1_accuracy']:.0%}"),
        ("Grounded drafts", f"{report['grounded_draft_rate']:.0%}"),
        ("Complete MCP traces", f"{report['mcp_trace_completeness']:.0%}"),
        ("Average latency", f"{report['average_latency_ms']} ms"),
    ]):
        column.metric(label, value)

    st.subheader("Quality gates")
    st.dataframe([{"Check": label, "Target": "Pass", "Status": "PASS" if passed else "REVIEW"} for label, passed in checks], width="stretch", hide_index=True)
    st.subheader("Route distribution")
    st.bar_chart(report["route_distribution"])
    st.subheader("Ticket classification and routing")
    st.dataframe([{
        "Ticket": row["ticket_id"],
        "Expected category": row["expected_category"],
        "Actual category": row["actual_category"],
        "Expected route": row["expected_route"],
        "Actual route": row["actual_route"],
        "Article": row["kb_article"] or "—",
        "MCP trace": "Complete" if row["mcp_trace_complete"] else "Review",
        "Result": "PASS" if row["passed"] else "REVIEW",
    } for row in report["rows"]], width="stretch", hide_index=True)
    st.subheader("Knowledge paraphrase retrieval")
    st.dataframe([{
        "Customer wording": row["query"],
        "Expected article": row["expected"],
        "Retrieved article": row["actual"] or "—",
        "Match": f"{row['score']:.0%}",
        "Result": "PASS" if row["passed"] else "REVIEW",
    } for row in report["retrieval_rows"]], width="stretch", hide_index=True)
    st.caption("Time-saved estimate: 4 minutes per safely auto-resolved ticket; this is an explicit demo assumption.")


def knowledge_base_view() -> None:
    st.subheader("Support knowledge base")
    st.write("Add approved support guidance here. ResolveFlow stores the article record in SQLite, creates an OpenAI dense embedding, indexes it in Qdrant, and makes it searchable through MCP.")
    if st.session_state.kb_notice:
        st.success(st.session_state.kb_notice)
        st.session_state.kb_notice = None

    current = load_knowledge_articles()
    existing_ids = {article["article_id"] for article in current}
    knowledge_action = st.radio(
        "Knowledge action",
        ["Add article", "Import CSV", "Ingest files", "Test search", "Clear knowledge"],
        horizontal=True,
        key="kb_action",
    )

    if knowledge_action == "Add article":
        with st.form("add-kb-article"):
            id_col, category_col = st.columns([1.2, 1])
            article_id = id_col.text_input("Article ID", placeholder="KB-CUSTOM-001")
            category = category_col.selectbox("Article category", ["technical", "billing", "account"])
            title = st.text_input("Article title", placeholder="How to reconnect a workspace device")
            excerpt = st.text_area("Approved answer", placeholder="Write the concise support guidance the agent may cite.", height=120)
            keywords = st.text_input("Search keywords", placeholder="sync, device, reconnect")
            if st.form_submit_button("Add to support knowledge base", type="primary"):
                clean_id = article_id.strip()
                if not clean_id or not title.strip() or not excerpt.strip():
                    st.error("Article ID, title, and approved answer are required.")
                elif clean_id in existing_ids:
                    st.error(f"Article ID {clean_id} already exists.")
                else:
                    new_article = {
                        "article_id": clean_id,
                        "title": title.strip(),
                        "category": category,
                        "excerpt": excerpt.strip(),
                        "keywords": [item.strip() for item in keywords.split(",") if item.strip()],
                    }
                    if run_knowledge_change(lambda: add_articles([new_article])) is not None:
                        st.session_state.kb_notice = f"{clean_id} was saved, embedded in Qdrant, and is ready for the next MCP search."
                        st.rerun()

    if knowledge_action == "Import CSV":
        st.write("Import up to 100 approved articles. You can append them or replace the complete searchable collection. Use a pipe (`|`) between keywords.")
        st.caption("Required: article_id, title, category, excerpt · Optional: keywords · Categories: technical, billing, account")
        template = "article_id,title,category,excerpt,keywords\nKB-CUSTOM-001,Reconnect a device,technical,Reconnect the device and sign in again,device|sync|reconnect\n"
        st.download_button("Download knowledge-base CSV template", template, "resolveflow-knowledge-template.csv", "text/csv")
        uploaded = st.file_uploader(
            "Choose a knowledge-base CSV",
            type=["csv"],
            key=f"knowledge-upload-{st.session_state.kb_csv_upload_version}",
        )
        if uploaded is not None:
            try:
                import_mode = st.radio(
                    "Import behavior",
                    ["Add to current knowledge", "Replace all searchable knowledge"],
                    horizontal=True,
                    help="Replace removes built-in and user-added articles before importing this file.",
                )
                imported = parse_knowledge_csv(uploaded, existing_ids if import_mode.startswith("Add") else set())
                st.success(f"{len(imported)} valid help articles found.")
                st.dataframe(imported[:10], width="stretch", hide_index=True)
                confirm_replace = True
                if import_mode.startswith("Replace"):
                    confirm_replace = st.checkbox("I understand this replaces every currently searchable article.")
                button_label = "Replace knowledge base" if import_mode.startswith("Replace") else "Add imported articles"
                if st.button(button_label, type="primary", disabled=not confirm_replace):
                    if import_mode.startswith("Replace"):
                        changed = run_knowledge_change(lambda: replace_articles(imported, source="Imported replacement"))
                        verb = "replaced the knowledge base with"
                    else:
                        changed = run_knowledge_change(lambda: add_articles(imported))
                        verb = "added"
                    if changed is not None:
                        st.session_state.kb_notice = f"Successfully {verb} {len(imported)} Qdrant-indexed help articles."
                        st.session_state.kb_csv_upload_version += 1
                        st.rerun()
            except (UnicodeDecodeError, ValueError) as exc:
                st.error(str(exc))

    if knowledge_action == "Ingest files":
        st.write("ResolveFlow extracts readable sections, creates OpenAI dense embeddings, and lets you preview them before indexing them in Qdrant.")
        st.download_button(
            "Markdown template",
            "# Help article title\n\nWrite approved customer guidance here. Include the steps to follow, information required, and when a person should review the request.\n\n# Another help article\n\nAdd another self-contained answer here.\n",
            "support-knowledge-template.md",
            "text/markdown",
        )
        document_category = st.selectbox("Category for these files", ["technical", "billing", "account"], key="document-category")
        document_mode = st.selectbox(
            "Document import behavior",
            ["Add to current knowledge", "Replace all searchable knowledge"],
            key="document-import-mode",
        )
        documents = st.file_uploader(
            "Choose knowledge files",
            type=["pdf", "md", "txt"],
            accept_multiple_files=True,
            key=f"knowledge-documents-{st.session_state.kb_document_upload_version}",
        )
        if documents:
            try:
                extracted = parse_knowledge_documents(
                    documents,
                    document_category,
                    existing_ids if document_mode.startswith("Add") else set(),
                )
                st.success(f"Extracted {len(extracted)} searchable sections from {len(documents)} file(s). Review this preview before importing.")
                st.dataframe(extracted[:10], width="stretch", hide_index=True)
                confirm_document_replace = True
                if document_mode.startswith("Replace"):
                    confirm_document_replace = st.checkbox(
                        "I understand these files will replace every currently searchable article.",
                        key="confirm-document-replace",
                    )
                document_button = "Replace with extracted knowledge" if document_mode.startswith("Replace") else "Add extracted knowledge"
                if st.button(document_button, type="primary", disabled=not confirm_document_replace):
                    if document_mode.startswith("Replace"):
                        changed = run_knowledge_change(lambda: replace_articles(extracted, source="Ingested document"))
                    else:
                        changed = run_knowledge_change(lambda: add_articles(extracted, source="Ingested document"))
                    if changed is not None:
                        st.session_state.kb_notice = f"Indexed {len(extracted)} sections from {len(documents)} knowledge file(s) in Qdrant."
                        st.session_state.kb_document_upload_version += 1
                        st.rerun()
            except (UnicodeDecodeError, ValueError) as exc:
                st.error(str(exc))

    if knowledge_action == "Test search":
        st.write("Try a customer-style question and inspect what the real MCP tool retrieves before running tickets.")
        with st.form("knowledge-search-test"):
            search_query = st.text_input("Test question", placeholder="I cannot get into my profile because I forgot the passcode")
            search_category = st.selectbox("Likely category", ["technical", "billing", "account"])
            if st.form_submit_button("Run MCP knowledge search", type="primary"):
                if search_query.strip():
                    try:
                        st.session_state.kb_search_results = MCPClient().search(search_query, search_category, 3)
                    except Exception as exc:
                        st.session_state.kb_search_results = []
                        st.error(f"MCP search is temporarily unavailable ({type(exc).__name__}). Try again shortly.")
                else:
                    st.session_state.kb_search_results = []
        if st.session_state.kb_search_results is not None:
            if st.session_state.kb_search_results:
                search_rows = [{
                    "Rank": index,
                    "Article": f"{match['article_id']} · {match['title']}",
                    "Overall": f"{match['score']:.0%}",
                    "Vector": f"{match.get('retrieval', {}).get('semantic_similarity', 0):.0%}",
                    "Term relevance": f"{match.get('retrieval', {}).get('lexical_relevance', 0):.0%}",
                    "Category match": "Yes" if match.get("retrieval", {}).get("category_preference") else "No",
                } for index, match in enumerate(st.session_state.kb_search_results, start=1)]
                st.dataframe(search_rows, width="stretch", hide_index=True)
                st.caption("This is the same `search_knowledge_base` MCP call used by ticket triage.")
            else:
                st.info("No knowledge was found. Add or import guidance before processing tickets.")

    if knowledge_action == "Clear knowledge":
        st.warning("Clearing knowledge affects future ticket runs. Existing ticket traces remain unchanged.")
        st.write(f"This permanently removes all **{len(current)} searchable help articles** from the local knowledge database.")
        confirmation = st.text_input("Type CLEAR KNOWLEDGE to confirm", key="clear-kb-confirmation")
        if st.button("Clear entire knowledge base", disabled=confirmation != "CLEAR KNOWLEDGE", type="primary"):
            removed = run_knowledge_change(lambda: clear_articles("all"))
            if removed is not None:
                st.session_state.kb_notice = f"Removed all {removed} searchable help articles from SQLite and Qdrant. Add or import new knowledge before triaging tickets."
                st.rerun()
        st.divider()
        st.write("Need the optional demo guidance again?")
        if st.button("Restore 15 starter articles"):
            restored = run_knowledge_change(lambda: restore_built_in_articles())
            if restored is not None:
                st.session_state.kb_notice = f"Restored and indexed {restored} starter help articles. Existing custom articles were kept."
                st.rerun()

    current = load_knowledge_articles()
    stats = knowledge_stats()
    compact_facts([
        ("Available articles", len(current)),
        ("Built-in articles", stats["built_in"]),
        ("Added / imported", stats["user_added"]),
        ("Search method", stats["search_method"]),
    ])

    st.markdown("### Available help articles")
    if not current:
        st.info("The knowledge base is empty. Add one article or import a CSV before processing tickets; weak or missing evidence will safely route tickets to a person.")
    table_rows = [{
        "Article ID": article["article_id"],
        "Title": article["title"],
        "Category": article["category"].title(),
        "Approved answer": article["excerpt"],
        "Keywords": ", ".join(article.get("keywords", [])),
        "Source": article["_source"],
    } for article in current]
    if table_rows:
        st.dataframe(table_rows, width="stretch", hide_index=True)
    export_rows = [{
        "article_id": article["article_id"],
        "title": article["title"],
        "category": article["category"],
        "excerpt": article["excerpt"],
        "keywords": "|".join(article.get("keywords", [])),
    } for article in current]
    st.download_button("Download all help articles (CSV)", csv_bytes(export_rows), "resolveflow-support-knowledge.csv", "text/csv", disabled=not export_rows)
    with st.expander("Storage and search details"):
        st.write("**Primary database:** SQLite — source of truth for article text, metadata, provenance, timestamps, and a recoverable embedding copy.")
        st.write(f"**Dedicated vector database:** {stats['vector_database']} — persistent semantic index used by MCP.")
        st.write(f"**Dense embeddings:** {stats['embedding_dimensions']}-dimensional `{stats['embedding_model']}` vectors generated by OpenAI.")
        st.write("**Hybrid retrieval:** Qdrant cosine similarity + BM25 term relevance. The candidate set is reranked with semantic, lexical, fuzzy, keyword, category, and reciprocal-rank signals.")
        st.write("**Grounded generation:** OpenAI receives only the top retrieved passages. Every factual paragraph needs an article citation; exact supporting quotes and citation IDs are validated before a draft can remain auto-resolved.")
        st.write("**MCP:** every ticket search calls the separate `search_knowledge_base` tool over stdio; the trace includes request, matches, ranks, and score breakdowns.")
        st.caption(f"SQLite: {stats['database']} · Qdrant: {stats['vector_database_path']}")


def architecture_view() -> None:
    st.subheader("How ResolveFlow works")
    st.write("ResolveFlow turns individual requests or CSV batches into a prioritized support queue. Every ticket follows the same visible workflow and nothing is sent to a customer automatically.")
    st.markdown(
        """
        <div class="flow">
          <span>Read ticket</span><b>→</b><span>Classify issue</span><b>→</b><span>Check risk</span><b>→</b>
          <span>MCP + Qdrant search</span><b>→</b><span>Hybrid rerank</span><b>→</b><span>Grounded draft</span><b>→</b><span>Validate citations</span>
        </div>
        <div class="how-grid">
          <div class="how-card"><div class="step">Step 1</div><b>Load requests</b>Paste one ticket, load 6 or 36 samples, or upload a CSV containing up to 50 tickets.</div>
          <div class="how-card"><div class="step">Step 2</div><b>Understand the issue</b>Assign billing, technical, or account category plus urgency and recommended team.</div>
          <div class="how-card"><div class="step">Step 3</div><b>Apply safety controls</b>Detect anger, threats, payment failure, security risk, missing information, and other blockers.</div>
          <div class="how-card"><div class="step">Step 4</div><b>Retrieve and rerank evidence</b>Call MCP, search OpenAI embeddings in Qdrant, combine BM25 keywords, then rerank the strongest passages.</div>
          <div class="how-card"><div class="step">Step 5</div><b>Generate only from evidence</b>OpenAI can prepare a draft only from retrieved passages, with article citations and exact support quotes.</div>
          <div class="how-card"><div class="step">Step 6</div><b>Validate or hold</b>Reject invalid citations or unsupported grounding, then route the ticket to a person instead of exposing the draft.</div>
        </div>
        <div class="how-callout"><b>Human-in-the-loop by design:</b> “Answer draft” means ready for review—not sent. A reviewer can approve, reroute, assign a team, add a note, and download the evidence trace.</div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### Inputs and outputs")
    input_col, output_col = st.columns(2)
    with input_col:
        st.markdown("#### What you can provide")
        st.markdown("- One manually entered ticket\n- Guided 6-ticket demonstration\n- Full 36-ticket operations sample\n- Editable 45-ticket CSV example\n- Your own CSV batch of up to 50 tickets")
    with output_col:
        st.markdown("#### What you receive")
        st.markdown("- Category, urgency, status, and response target\n- Recommended team and safe route\n- Matched help article and confidence\n- Draft response or human handoff note\n- Searchable queue, CSV export, and JSON trace")

    st.markdown("### Active AI configuration")
    st.success(f"OpenAI is the ticket-classification provider · `{OPENAI_MODEL}`")
    st.write("The dashboard always uses the OpenAI configuration from `.env`. If the model or API becomes unavailable, the trace records `MODEL_ERROR` and routes the ticket to a person instead of silently switching classifiers.")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Real MCP boundary")
        st.code("Ticket workflow\n    │ MCP over stdio\n    ▼\nKnowledge search server\n    ├─ SQLite article store\n    └─ Qdrant dense vectors\n          │ hybrid rerank\n          ▼\nGrounded OpenAI draft + validator", language="text")
        st.write(f"MCP is the tool connection. `stdio` means standard input/output. The separate server embeds the query with `{OPENAI_EMBEDDING_MODEL}`, searches Qdrant, adds BM25 keyword evidence, and returns reranked passages with score details.")
    with right:
        st.markdown("#### Safety always wins")
        st.write("Deterministic controls override model suggestions. High urgency, security risk, anger, payment failure, legal/refund risk, weak evidence, model failure, or MCP failure can never produce an automatic answer draft.")
        st.write("Missing order or invoice details produce a focused clarification question instead of a guess.")

    st.markdown("### Route outcomes")
    st.table([
        {"User-facing route": "Answer draft", "System route": "AUTO_RESOLVE", "When used": "Low-risk ticket with a strong approved help-article match"},
        {"User-facing route": "Needs clarification", "System route": "CLARIFY", "When used": "A specific customer detail is required before work can continue"},
        {"User-facing route": "Human review", "System route": "ESCALATE", "When used": "Risk, urgency, uncertainty, weak evidence, or a system failure"},
    ])

    st.markdown("### Response targets")
    st.table([
        {"Urgency": "Critical", "Target": "15 minutes", "Examples": "Account takeover or immediate security risk"},
        {"Urgency": "High", "Target": "1 hour", "Examples": "Payment failure or urgent account lockout"},
        {"Urgency": "Medium", "Target": "8 hours", "Examples": "Billing questions and degraded functionality"},
        {"Urgency": "Low", "Target": "24 hours", "Examples": "Routine how-to questions"},
    ])

    st.markdown("### What the evidence trace proves")
    st.write("Every result records the ticket ID, correlation ID, state transitions, timestamps, durations, safety controls, MCP request and reranked matches, citations, grounding-validation result, decision threshold, final route, and reviewer audit events—without exposing hidden chain-of-thought.")


def main() -> None:
    st.set_page_config(page_title="ResolveFlow AI", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
    setup_state()
    apply_theme()
    with st.sidebar:
        st.markdown("## ◈ ResolveFlow")
        st.caption("Triage control center")
        page = st.radio("Workspace", ["Ticket triage", "Support knowledge", "Quality check", "How it works"], label_visibility="collapsed")
        st.divider()
        st.markdown("**AI processing**")
        if HAS_OPENAI_KEY and openai_is_active():
            st.markdown('<span class="status-pill">OpenAI active</span>', unsafe_allow_html=True)
            st.caption(f"Model: {OPENAI_MODEL}")
        else:
            st.markdown('<span class="status-pill empty">OpenAI unavailable</span>', unsafe_allow_html=True)
            st.caption("Check OPENAI_API_KEY and OPENAI_MODEL in .env. Tickets safely route to human review while unavailable.")
        mcp_ok = any(item.get("mcp_connected") for item in st.session_state.results.values())
        sidebar_kb_stats = knowledge_stats()
        kb_count = sidebar_kb_stats["articles"]
        kb_status = "Connected" if mcp_ok else (f"Ready · {kb_count} articles" if kb_count else "Empty · add knowledge")
        st.caption(f"Support knowledge base: {kb_status}")
        with st.expander("Technical details"):
            st.caption(f"Model setting: {OPENAI_MODEL}")
            st.caption(f"Embedding model: {sidebar_kb_stats['embedding_model']}")
            st.caption("Vector database: Qdrant · persistent local mode")
            st.caption("Retrieval: dense + BM25 + rerank")
            st.caption(f"Minimum help-article match: {KB_THRESHOLD:.0%}")
            st.caption("MCP transport: stdio")
        st.divider()
        st.caption("Guardrails always on · Customer sending disabled")
    render_header()
    if page == "Ticket triage":
        operations_view()
    elif page == "Support knowledge":
        knowledge_base_view()
    elif page == "Quality check":
        evaluation_view()
    else:
        architecture_view()


if __name__ == "__main__":
    main()
