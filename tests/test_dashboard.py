from streamlit.testing.v1 import AppTest
from pathlib import Path

from app.dashboard import (
    load_knowledge_articles,
    load_tickets,
    parse_knowledge_csv,
    parse_knowledge_documents,
    parse_uploaded_batch,
    response_target,
)


def test_dashboard_demo_and_navigation_render_without_errors():
    dashboard = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
    app = AppTest.from_file(dashboard, default_timeout=60).run()
    assert not app.exception
    assert app.radio[0].options == ["Ticket triage", "Support knowledge", "Quality check", "How it works"]
    assert app.button[0].label == "Load sample batch"
    assert app.button[1].label == "Clear all"
    assert next(item for item in app.selectbox if item.label == "Sample batch").options == ["Guided demo · 6 tickets", "Operations batch · all 36 tickets"]
    assert any("OpenAI active" in item.value for item in app.markdown)
    assert len(load_tickets()) == 36
    assert [metric.value for metric in app.metric[:5]] == ["0", "0", "0", "0", "0 min"]

    app.button[0].click().run(timeout=60)
    assert not app.exception
    assert [metric.value for metric in app.metric[:5]] == ["6", "2", "3", "2", "8 min"]
    assert len(app.metric) == 5  # Detail and MCP evidence use compact cards, not oversized KPI typography.
    assert len(app.dataframe) == 1
    assert len(app.get("download_button")) == 4
    assert len(app.expander) >= 7

    next(item for item in app.selectbox if item.label == "Final route").set_value("ESCALATE").run()
    next(item for item in app.text_input if item.label == "Reviewer note").input("Send to a specialist for review.").run()
    next(item for item in app.button if item.label == "Record review decision").click().run()
    assert len(app.session_state.audit_log) == 1
    assert len(app.dataframe) == 2

    app.button[1].click().run()
    assert [metric.value for metric in app.metric[:5]] == ["0", "0", "0", "0", "0 min"]

    next(item for item in app.radio if item.label == "Workspace").set_value("Support knowledge").run()
    assert not app.exception
    assert any(button.label == "Add to support knowledge base" for button in app.button)
    assert any(button.label == "Download all help articles (CSV)" for button in app.get("download_button"))
    assert len(load_knowledge_articles()) >= 15

    knowledge_action = next(item for item in app.radio if item.label == "Knowledge action")
    knowledge_action.set_value("Ingest files").run()
    assert next(item for item in app.radio if item.label == "Knowledge action").value == "Ingest files"
    next(item for item in app.radio if item.label == "Knowledge action").set_value("Clear knowledge").run()
    assert any(item.label == "Type CLEAR KNOWLEDGE to confirm" for item in app.text_input)
    assert any(button.label == "Clear entire knowledge base" for button in app.button)
    assert not any(item.label == "What should be removed?" for item in app.selectbox)

    next(item for item in app.radio if item.label == "Workspace").set_value("Quality check").run()
    assert not app.exception
    assert any(button.label == "Run live OpenAI quality check" for button in app.button)

    next(item for item in app.radio if item.label == "Workspace").set_value("How it works").run()
    assert not app.exception
    assert any("Real MCP boundary" in markdown.value for markdown in app.markdown)


def test_theme_has_explicit_light_contrast_rules():
    root = Path(__file__).resolve().parents[1]
    theme = root.joinpath(".streamlit/config.toml").read_text()
    dashboard = root.joinpath("app/dashboard.py").read_text()
    assert 'base = "light"' in theme
    assert 'textColor = "#172033"' in theme
    for selector in [
        "stMetricLabel", "stMetricValue", "stWidgetLabel", "baseButton-secondary",
        "stExpanderDetails", "stTextInput", "stSelectbox", "stFileUploaderDropzone",
        'data-baseweb="tab"', 'role="listbox"', "focus-within", "focus-visible", "span.status-pill",
    ]:
        assert selector in dashboard
    assert "#06633f" in dashboard
    assert "border:1.5px solid #8792a5" in dashboard
    assert '.stApp div[data-baseweb="select"] > div' in dashboard
    assert '[data-testid="stNumberInput"]' in dashboard


class UploadedCSV:
    def __init__(self, text: str, name: str = "upload.csv"):
        self.text = text
        self.name = name

    def getvalue(self):
        return self.text.encode()


def test_csv_batch_import_validation():
    rows = parse_uploaded_batch(UploadedCSV("ticket_id,subject,body\nB-1,Login help,I cannot log in\n"))
    assert rows[0]["ticket_id"] == "B-1"
    assert rows[0]["customer_id"] is None

    try:
        parse_uploaded_batch(UploadedCSV("ticket_id,subject\nB-1,Login help\n"))
    except ValueError as exc:
        assert "subject and body" in str(exc)
    else:
        raise AssertionError("Missing body column should fail validation")

    root = Path(__file__).resolve().parents[1]
    example = root / "app" / "fixtures" / "realistic_ticket_batch_45.csv"
    assert len(parse_uploaded_batch(UploadedCSV(example.read_text()))) == 45
    ecommerce = root / "sample_data" / "northstar_support_tickets_50.csv"
    assert len(parse_uploaded_batch(UploadedCSV(ecommerce.read_text()))) == 50

    try:
        parse_uploaded_batch(UploadedCSV("ticket_id,subject,body\nB-1,One,Message\nB-1,Two,Message\n"))
    except ValueError as exc:
        assert "appears more than once" in str(exc)
    else:
        raise AssertionError("Duplicate ticket IDs should fail validation")

    assert response_target("critical") == "15 minutes"
    assert response_target("low") == "24 hours"


def test_knowledge_csv_validation():
    valid = UploadedCSV(
        "article_id,title,category,excerpt,keywords\n"
        "KB-NEW-1,Reconnect device,technical,Reconnect and sign in again,device|sync\n"
    )
    articles = parse_knowledge_csv(valid, {"KB-001"})
    assert articles[0]["keywords"] == ["device", "sync"]

    root = Path(__file__).resolve().parents[1]
    ecommerce = root / "sample_data" / "northstar_ecommerce_knowledge.csv"
    ecommerce_articles = parse_knowledge_csv(UploadedCSV(ecommerce.read_text()), set())
    assert len(ecommerce_articles) == 30
    assert len({article["article_id"] for article in ecommerce_articles}) == 30

    duplicate = UploadedCSV(
        "article_id,title,category,excerpt\n"
        "KB-001,Duplicate,technical,Duplicate answer\n"
    )
    try:
        parse_knowledge_csv(duplicate, {"KB-001"})
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("Existing knowledge article IDs should be rejected")


def test_markdown_knowledge_ingestion_creates_reviewable_sections():
    upload = UploadedCSV(
        "# Device setup\n\nPair the device from workspace settings.\n\n# Troubleshooting\n\nReconnect and authenticate again.",
        "device-guide.md",
    )
    articles = parse_knowledge_documents([upload], "technical", set())
    assert articles[0]["article_id"].startswith("DOC-DEVICE-GUIDE-")
    assert articles[0]["category"] == "technical"
    assert "Pair the device" in articles[0]["excerpt"]


def test_company_markdown_preserves_sections_and_category_labels():
    root = Path(__file__).resolve().parents[1]
    markdown = root / "sample_data" / "northstar_ecommerce_knowledge.md"
    articles = parse_knowledge_documents([UploadedCSV(markdown.read_text(), markdown.name)], "account", set())
    assert len(articles) == 30
    assert {article["category"] for article in articles} == {"technical", "billing", "account"}
    assert any("Password reset" in article["title"] and article["category"] == "technical" for article in articles)
