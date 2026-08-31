PYTHON := .venv/bin/python
STREAMLIT := .venv/bin/streamlit

.PHONY: setup run mcp test eval

setup:
	python3 -m venv .venv
	.venv/bin/python -m pip install -e .

run:
	$(STREAMLIT) run app/dashboard.py

mcp:
	$(PYTHON) -m app.mcp_server

test:
	$(PYTHON) -m pytest -q

eval:
	$(PYTHON) -m app.eval
