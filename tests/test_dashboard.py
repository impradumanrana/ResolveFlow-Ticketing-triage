from streamlit.testing.v1 import AppTest
from pathlib import Path

from app.dashboard import load_tickets, parse_uploaded_batch, response_target


def test_dashboard_demo_and_navigation_render_without_errors():
    dashboard = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
    app = AppTest.from_file(dashboard, default_timeout=60).run()
    assert not app.exception
    assert app.radio[0].options == ["Ticket triage", "Quality check", "How it works"]
    assert app.button[0].label == "Load sample batch"
    assert app.button[1].label == "Clear all"
    assert next(item for item in app.selectbox if item.label == "Sample batch").options == ["Guided demo · 6 tickets", "Operations batch · all 36 tickets"]
    assert next(item for item in app.selectbox if item.label == "Processing mode").value == "Reliable demo classifier"
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

    app.radio[0].set_value("Quality check").run()
    assert not app.exception
    assert any(button.label == "Run accuracy check" for button in app.button)

    app.radio[0].set_value("How it works").run()
    assert not app.exception
    assert any("Real MCP boundary" in markdown.value for markdown in app.markdown)


def test_theme_has_explicit_light_contrast_rules():
    root = Path(__file__).resolve().parents[1]
    theme = root.joinpath(".streamlit/config.toml").read_text()
    dashboard = root.joinpath("app/dashboard.py").read_text()
    assert 'base = "light"' in theme
    assert 'textColor = "#172033"' in theme
    for selector in ["stMetricLabel", "stMetricValue", "stWidgetLabel", "baseButton-secondary", "stExpanderDetails"]:
        assert selector in dashboard


class UploadedCSV:
    def __init__(self, text: str):
        self.text = text

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

    try:
        parse_uploaded_batch(UploadedCSV("ticket_id,subject,body\nB-1,One,Message\nB-1,Two,Message\n"))
    except ValueError as exc:
        assert "appears more than once" in str(exc)
    else:
        raise AssertionError("Duplicate ticket IDs should fail validation")

    assert response_target("critical") == "15 minutes"
    assert response_target("low") == "24 hours"
