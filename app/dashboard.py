from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

from app.cli import triage_ticket
from app.config import KB_THRESHOLD, OPENAI_MODEL, USING_DEMO_PROVIDER
from app.eval import build_eval_report
from app.models import Ticket
from app.providers import DeterministicProvider


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
    "draft_clarification": "Prepare clarification question",
    "create_escalation": "Prepare human handoff",
    "observe": "Assemble final result",
}


def load_tickets() -> list[dict[str, Any]]:
    return json.loads(APP_ROOT.joinpath("fixtures/tickets.json").read_text())


def setup_state() -> None:
    defaults = {
        "results": {},
        "ticket_inputs": {},
        "audit_log": [],
        "selected_ticket": DEMO_IDS[0],
        "eval_report": None,
        "provider_mode": "Reliable demo classifier",
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
        [data-testid="stMain"] input::placeholder,
        [data-testid="stMain"] textarea::placeholder { color:#7b8497 !important; opacity:1; }
        [data-testid="stMain"] [data-baseweb="select"] > div { background:#fff !important; color:#172033 !important; border-color:#cfd5e3 !important; }
        [data-testid="stMain"] [data-baseweb="select"] span { color:#172033 !important; }
        [data-testid="stMetric"] { background:#fff; border:1px solid #dfe3ee; padding:18px; border-radius:16px; box-shadow:0 8px 24px rgba(17,24,39,.05); min-height:148px; }
        [data-testid="stMetricLabel"] p { color:#536178 !important; font-weight:700 !important; }
        [data-testid="stMetricValue"] { color:#111b35 !important; font-weight:750 !important; }
        [data-testid="stMetricDelta"] div { color:#087443 !important; font-weight:650 !important; }
        [data-testid="stMetricDelta"] svg { fill:#087443 !important; }
        [data-testid="stMain"] button[kind="secondary"] { background:#fff !important; color:#26334d !important; border:1px solid #bfc7d8 !important; }
        [data-testid="stMain"] button[kind="secondary"] p { color:#26334d !important; font-weight:700 !important; }
        [data-testid="stMain"] button[kind="primary"] { background:#4f46e5 !important; border-color:#4f46e5 !important; }
        [data-testid="stMain"] button[kind="primary"] p { color:#fff !important; font-weight:750 !important; }
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
        .flow { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:14px 0 22px; }
        .flow span { background:#fff; border:1px solid #dfe3f1; border-radius:10px; padding:9px 12px; font-size:.82rem; font-weight:700; }
        .flow b { color:#7a8295; }
        div[data-testid="stDataFrame"] { border:1px solid #e7e9f3; border-radius:14px; overflow:hidden; }
        [data-testid="stSidebar"] { background:#0b1739; }
        [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p, [data-testid="stSidebar"] label, [data-testid="stSidebar"] span { color:#eef2ff !important; }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color:#aeb9d4 !important; }
        [data-testid="stSidebar"] [data-testid="stExpander"],
        [data-testid="stSidebar"] [data-testid="stExpander"] details,
        [data-testid="stSidebar"] [data-testid="stExpander"] summary,
        [data-testid="stSidebar"] [data-testid="stExpanderDetails"] { background:#13254f !important; border-color:#31456f !important; }
        [data-testid="stSidebar"] [data-testid="stExpander"] summary:hover { background:#1a315f !important; }
        [data-testid="stSidebar"] [data-testid="stExpander"] svg { fill:#dbe4ff !important; color:#dbe4ff !important; }
        [data-testid="stSidebar"] [data-testid="stExpander"] p,
        [data-testid="stSidebar"] [data-testid="stExpander"] span { color:#eaf0ff !important; }
        @media (max-width:900px) { .quick-start, .compact-grid, .compact-grid.mcp-grid { grid-template-columns:1fr; } [data-testid="stMetric"] { min-height:auto; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def ticket_from_fixture(raw: dict[str, Any]) -> Ticket:
    return Ticket(**{key: value for key, value in raw.items() if key in Ticket.model_fields})


def run_ticket(raw: dict[str, Any]) -> dict[str, Any]:
    provider = None if st.session_state.get("provider_mode") == "Configured hosted model" else DeterministicProvider()
    return triage_ticket(ticket_from_fixture(raw), provider=provider).model_dump()


def run_ticket_batch(raw_tickets: list[dict[str, Any]], label: str) -> None:
    progress = st.progress(0, text="Starting the inspectable triage graph…")
    for index, raw in enumerate(raw_tickets):
        ticket_id = raw["ticket_id"]
        progress.progress(index / len(raw_tickets), text=f"Processing {ticket_id} · {index + 1} of {len(raw_tickets)}")
        st.session_state.results[ticket_id] = run_ticket(raw)
        st.session_state.ticket_inputs[ticket_id] = raw
    progress.empty()
    st.toast(f"{label} is ready. Choose a ticket below to inspect the decision.", icon="✅")


def run_sample_batch(batch_name: str) -> None:
    fixtures = load_tickets()
    ticket_ids = SAMPLE_BATCHES[batch_name]
    selected = fixtures if ticket_ids is None else [row for ticket_id in ticket_ids for row in fixtures if row["ticket_id"] == ticket_id]
    run_ticket_batch(selected, batch_name)


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
        ("Search action", "Find approved help article"),
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
    action_a, action_b, action_c, action_d = st.columns([1.55, 1.2, 1, 1.8])
    batch_name = action_a.selectbox("Sample batch", list(SAMPLE_BATCHES), label_visibility="collapsed")
    if action_b.button("Load sample batch", type="primary", width="stretch"):
        run_sample_batch(batch_name)
        results = st.session_state.results
    if action_c.button("Clear all", width="stretch"):
        reset_demo()
        results = {}
    action_d.markdown('<span class="status-pill">Support knowledge base ready</span>', unsafe_allow_html=True)
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

    with st.expander("Add one ticket manually"):
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

    with st.expander("Import a ticket batch from CSV"):
        st.write("Download a file, replace the examples with real support requests, then upload it here. Up to 50 tickets are processed through the same safety and support-knowledge workflow.")
        st.caption("Required: subject and body · Optional: ticket_id and customer_id · Do not include passwords, payment-card numbers, or other sensitive secrets.")
        dl_a, dl_b = st.columns(2)
        dl_a.download_button(
            "Download empty CSV template",
            "ticket_id,customer_id,subject,body\n",
            "resolveflow-empty-template.csv",
            "text/csv",
            width="stretch",
        )
        dl_b.download_button(
            "Download 45-ticket example",
            APP_ROOT.joinpath("fixtures/realistic_ticket_batch_45.csv").read_bytes(),
            "resolveflow-realistic-45-tickets.csv",
            "text/csv",
            width="stretch",
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

    st.subheader("Ticket queue")
    if not results:
        st.info("Start by loading the six sample tickets above, or open ‘Add one ticket manually’ to paste a customer request.")
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
    st.subheader("Quality and safety check")
    st.write("Run 15 pre-labelled examples to check routing accuracy, risk detection, and unsafe automation.")
    if st.button("Run accuracy check", type="primary"):
        with st.spinner("Evaluating 15 labelled cases through the real MCP boundary…"):
            st.session_state.eval_report = build_eval_report()
    report = st.session_state.eval_report
    if not report:
        st.info("Run the evaluation to generate measured results. No metrics are precomputed or fabricated.")
        return
    st.caption(report["run_label"])
    a, b, c, d, e = st.columns(5)
    a.metric("Category accuracy", f"{report['category_accuracy']:.0%}")
    b.metric("Route accuracy", f"{report['route_accuracy']:.0%}")
    c.metric("High-risk recall", f"{report['high_risk_recall']:.0%}")
    d.metric("Unsafe auto-resolves", report["unsafe_auto_resolves"])
    e.metric("Avg latency", f"{report['average_latency_ms']} ms")
    st.subheader("Route distribution")
    st.bar_chart(report["route_distribution"])
    st.subheader("Expected vs actual")
    st.dataframe([{
        "Ticket": row["ticket_id"],
        "Expected category": row["expected_category"],
        "Actual category": row["actual_category"],
        "Expected route": row["expected_route"],
        "Actual route": row["actual_route"],
        "Result": "PASS" if row["passed"] else "REVIEW",
    } for row in report["rows"]], width="stretch", hide_index=True)
    st.caption("Time-saved estimate: 4 minutes per safely auto-resolved ticket; this is an explicit demo assumption.")


def architecture_view() -> None:
    st.subheader("How ResolveFlow makes a decision")
    st.markdown(
        """
        <div class="flow">
          <span>Perceive</span><b>→</b><span>Classify</span><b>→</b><span>Risk guard</span><b>→</b>
          <span>Search support knowledge base</span><b>→</b><span>Decide</span><b>→</b><span>Act</span><b>→</b><span>Observe</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    left, right = st.columns(2)
    with left:
        st.markdown("#### Real MCP boundary")
        st.code("Dashboard / CLI\n    │ stdio\n    ▼\nFastMCP server\n    │\n    ▼\n15-article FAQ fixture", language="text")
        st.write("The graph launches a separate Python process and calls search_knowledge_base(query, category, top_k) through an MCP client session.")
    with right:
        st.markdown("#### Safety policy")
        st.write("Deterministic controls override any model suggestion. High urgency, security risk, anger, payment failure, legal/refund risk, missing data, a weak help-article match, model failure, or MCP failure can never auto-resolve.")
    st.markdown("#### Route contract")
    st.table([
        {"Route": "AUTO_RESOLVE", "Meaning": "Strong grounded match; draft remains review-ready"},
        {"Route": "CLARIFY", "Meaning": "A required customer fact is missing"},
        {"Route": "ESCALATE", "Meaning": "Risk, uncertainty, weak evidence, or system failure"},
    ])


def main() -> None:
    st.set_page_config(page_title="ResolveFlow AI", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
    setup_state()
    apply_theme()
    with st.sidebar:
        st.markdown("## ◈ ResolveFlow")
        st.caption("Triage control center")
        page = st.radio("Workspace", ["Ticket triage", "Quality check", "How it works"], label_visibility="collapsed")
        st.divider()
        st.markdown("**System status**")
        provider_options = ["Reliable demo classifier"]
        if not USING_DEMO_PROVIDER:
            provider_options.append("Configured hosted model")
        if st.session_state.provider_mode not in provider_options:
            st.session_state.provider_mode = provider_options[0]
        st.selectbox("Processing mode", provider_options, key="provider_mode")
        st.caption("Demo mode is repeatable. Hosted mode uses your configured provider and safely escalates on failure.")
        mcp_ok = any(item.get("mcp_connected") for item in st.session_state.results.values())
        st.caption(f"Support knowledge base: {'Connected' if mcp_ok else 'Ready'}")
        with st.expander("Technical details"):
            st.caption(f"Model setting: {OPENAI_MODEL}")
            st.caption(f"Minimum help-article match: {KB_THRESHOLD:.0%}")
            st.caption("MCP transport: stdio")
        st.divider()
        st.caption("Guardrails always on · Customer sending disabled")
    render_header()
    if page == "Ticket triage":
        operations_view()
    elif page == "Quality check":
        evaluation_view()
    else:
        architecture_view()


if __name__ == "__main__":
    main()
