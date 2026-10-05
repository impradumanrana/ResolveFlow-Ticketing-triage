PYTHON := .venv/bin/python
STREAMLIT := .venv/bin/streamlit

# One list, used by every target and by CI (which calls these targets rather
# than repeating them). Three copies of this list is what let the CI job drift
# four phases behind the Makefile, so there is now one.
#
# `tests` is linted whole: a per-file list means a new test file is unlinted
# until somebody remembers to add it.
LINT_PATHS := app/api app/security app/privacy.py app/pilot.py app/retention.py app/knowledge \
	app/mailbox app/ingestion app/rules app/gateway app/triage app/review \
	migrations scripts/export_openapi.py scripts/validate_infra.py \
	scripts/bootstrap_organization.py scripts/triage_demo.py scripts/quality_check.py \
	scripts/compare_retrieval.py scripts/acceptance_run.py scripts/smoke_test.py \
	scripts/access_review.py tests

# The MVP core (app/graph.py, app/providers.py, app/dashboard.py and friends) is
# deliberately absent: it predates the typed surface and is covered by its own
# tests. Everything built for the client is here.
TYPE_PATHS := app/api app/security app/privacy.py app/pilot.py app/retention.py app/knowledge \
	app/mailbox app/ingestion app/rules app/gateway app/triage app/review \
	scripts/export_openapi.py scripts/validate_infra.py scripts/bootstrap_organization.py

.PHONY: setup run api mcp test eval contract migration-check retrieval-check infra-check python-check security-check smoke access-review web-install web web-check check client-up

setup:
	python3 -m venv .venv
	.venv/bin/python -m pip install -e .

run:
	$(STREAMLIT) run app/dashboard.py

api:
	$(PYTHON) -m uvicorn app.api.main:app --host 127.0.0.1 --port 8080 --reload

mcp:
	$(PYTHON) -m app.mcp_server

test:
	$(PYTHON) -m pytest -q

eval:
	$(PYTHON) -m app.eval

contract:
	$(PYTHON) -m scripts.export_openapi

migration-check:
	$(PYTHON) -m alembic upgrade head --sql

retrieval-check:
	RESOLVEFLOW_TEST_MODE=1 $(PYTHON) -m scripts.compare_retrieval

infra-check:
	$(PYTHON) -m scripts.validate_infra --with-terraform

smoke:
	$(PYTHON) -m scripts.smoke_test --api-url "$(API_URL)" --web-url "$(WEB_URL)" --database-url "$(DATABASE_URL)"

access-review:
	$(PYTHON) -m scripts.access_review --database-url "$(DATABASE_URL)" --organization "$(ORGANIZATION_ID)"

security-check:
	$(PYTHON) -m pip_audit --strict --requirement requirements.txt --requirement requirements-dev.txt
	npm audit --omit=dev --audit-level=high
	@echo "--- build-time packages (reported, not blocking) ---"
	-npm audit --audit-level=high

python-check:
	$(PYTHON) -m ruff check $(LINT_PATHS)
	$(PYTHON) -m mypy $(TYPE_PATHS)

web-install:
	npm ci

web:
	npm run dev

web-check:
	npm run lint
	npm run typecheck
	npm test
	npm run build

check:
	$(PYTHON) -m pytest -q
	$(PYTHON) -m ruff check $(LINT_PATHS)
	$(PYTHON) -m alembic upgrade head --sql
	$(PYTHON) -m mypy $(TYPE_PATHS)
	$(PYTHON) -m scripts.validate_infra
	npm run lint
	npm run typecheck
	npm test
	npm run build

client-up:
	docker compose -f compose.client.yml up --build

.PHONY: quality-check
quality-check:
	RESOLVEFLOW_TEST_MODE=1 $(PYTHON) scripts/quality_check.py

.PHONY: triage-demo
triage-demo:
	RESOLVEFLOW_TEST_MODE=1 $(PYTHON) scripts/triage_demo.py
