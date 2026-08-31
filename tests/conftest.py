from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_test_runtime(tmp_path_factory):
    """Keep rendered tests and MCP subprocesses away from the user's live data."""
    runtime_dir = tmp_path_factory.mktemp("resolveflow-runtime")
    previous_db = os.environ.get("KB_DB_PATH")
    previous_mode = os.environ.get("RESOLVEFLOW_TEST_MODE")
    os.environ["KB_DB_PATH"] = str(runtime_dir / "knowledge.db")
    os.environ["RESOLVEFLOW_TEST_MODE"] = "1"
    yield
    if previous_db is None:
        os.environ.pop("KB_DB_PATH", None)
    else:
        os.environ["KB_DB_PATH"] = previous_db
    if previous_mode is None:
        os.environ.pop("RESOLVEFLOW_TEST_MODE", None)
    else:
        os.environ["RESOLVEFLOW_TEST_MODE"] = previous_mode
