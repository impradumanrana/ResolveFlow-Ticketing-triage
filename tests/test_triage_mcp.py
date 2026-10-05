"""C11: the knowledge boundary.

These run the real server in a real subprocess over stdio. What is proven here
is the boundary itself - the tool contract, the result shape, reuse, recovery,
and that a failure is never an empty result list - because "the model only sees
retrieved evidence" rests on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.triage.knowledge_mcp import (
    RESULT_FIELDS,
    TOOL_NAME,
    BackendUnavailable,
    FixtureBackend,
    build_backend,
    search_knowledge_base,
)
from app.triage.mcp_client import KnowledgeMcpClient, McpUnavailable

FIXTURE = Path("app/fixtures/quality/knowledge_fixture.json").resolve()
ENV = {"RESOLVEFLOW_MCP_FIXTURE": str(FIXTURE), "RESOLVEFLOW_TEST_MODE": "1"}


@pytest.fixture(scope="module")
def client():
    with KnowledgeMcpClient(env=ENV) as connected:
        yield connected


def test_a_search_crosses_the_process_boundary_and_returns_citable_passages(client):
    results = client.search("I forgot my password and need a reset link", "technical", top_k=3)

    assert results, "the corpus should answer a question it covers"
    assert results[0]["article_id"] == "KB-001"
    for result in results:
        assert set(RESULT_FIELDS).issubset(result)
        assert 0 < result["score"] <= 1
    # The excerpt is the stored text, so a draft's quote can be checked against it.
    assert "Forgot password" in results[0]["excerpt"]
    assert client.connected and client.last_duration_ms > 0
    assert client.last_request == {
        "query": "I forgot my password and need a reset link",
        "category": "technical",
        "top_k": 3,
    }


def test_the_server_process_is_reused_between_searches(client):
    client.search("password reset", "technical")
    first = client.last_duration_ms
    client.search("cancel my subscription", "billing")
    # Start-up is paid once; a reused session answers in a fraction of it.
    assert client.last_duration_ms < max(first, 50.0)


def test_a_category_is_a_bonus_not_a_filter(client):
    # KB-014 is an account article; a technical classification must still find it.
    results = client.search("change the display name on my profile", "technical", top_k=3)
    assert results[0]["article_id"] == "KB-014"


def test_top_k_is_bounded_and_honoured(client):
    assert len(client.search("password", "technical", top_k=1)) == 1
    assert len(client.search("password", "technical", top_k=99)) <= 5


def test_a_question_the_corpus_cannot_answer_returns_nothing_rather_than_noise(client):
    assert client.search("zzzzq unrelated aardvark xylophone", "technical") == []


def test_an_empty_query_is_refused_rather_than_answered(client):
    with pytest.raises(McpUnavailable):
        client.search("   ", "technical")


def test_a_failed_search_closes_the_session_and_the_next_one_reconnects(monkeypatch):
    """A broken session must not be handed to the next conversation."""
    with KnowledgeMcpClient(env=ENV) as client:
        client.search("password", "technical")
        first_session = client._session
        assert first_session is not None

        async def broken(*_args, **_kwargs):
            raise RuntimeError("stdio stream closed")

        monkeypatch.setattr(client, "_call", broken)
        with pytest.raises(McpUnavailable):
            client.search("password", "technical")
        assert client._session is None, "the failed session was kept"
        assert client.connected is False

        monkeypatch.undo()
        results = client.search("password", "technical")
        assert results[0]["article_id"] == "KB-001"
        assert client._session is not first_session, "a new session was not started"


def test_a_server_that_cannot_start_is_unavailable_not_empty():
    with KnowledgeMcpClient(
        env={"RESOLVEFLOW_MCP_FIXTURE": "/nonexistent/corpus.json", "RESOLVEFLOW_TEST_MODE": "1"}
    ) as client:
        with pytest.raises(McpUnavailable):
            client.search("password", "technical")
        assert client.connected is False


def test_a_slow_server_times_out_rather_than_hanging():
    with KnowledgeMcpClient(env=ENV, timeout=0.001) as client:
        with pytest.raises(McpUnavailable):
            client.search("password", "technical")


def test_an_unconfigured_server_refuses_to_serve(monkeypatch):
    for name in ("RESOLVEFLOW_MCP_FIXTURE", "DATABASE_URL", "RESOLVEFLOW_ORGANIZATION_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(BackendUnavailable):
        build_backend()


def test_the_fixture_corpus_is_unavailable_outside_test_mode(monkeypatch):
    monkeypatch.setenv("RESOLVEFLOW_MCP_FIXTURE", str(FIXTURE))
    monkeypatch.setenv("RESOLVEFLOW_TEST_MODE", "0")
    with pytest.raises(BackendUnavailable, match="test affordance"):
        build_backend()


def test_the_production_backend_is_the_one_configured_by_the_environment(monkeypatch):
    monkeypatch.delenv("RESOLVEFLOW_MCP_FIXTURE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user@localhost/db")
    monkeypatch.setenv("RESOLVEFLOW_ORGANIZATION_ID", "00000000-0000-0000-0000-00000000000a")
    backend = build_backend()
    assert type(backend).__name__ == "PostgresBackend"
    assert backend.organization_id == "00000000-0000-0000-0000-00000000000a"


def test_the_tool_validates_its_own_arguments(monkeypatch):
    monkeypatch.setenv("RESOLVEFLOW_TEST_MODE", "1")
    monkeypatch.setattr("app.triage.knowledge_mcp._backend", FixtureBackend(FIXTURE))
    with pytest.raises(ValueError):
        search_knowledge_base(query="", category="technical")
    assert search_knowledge_base(query="password", category="technical", top_k=0)


def test_the_client_refuses_a_result_shape_it_cannot_cite():
    from app.triage.mcp_client import _decode

    class Item:
        def __init__(self, text):
            self.text = text

    good = json.dumps([{field: "x" for field in RESULT_FIELDS}])
    assert _decode([Item(good)])
    with pytest.raises(McpUnavailable):
        _decode([Item(json.dumps([{"article_id": "KB-001"}]))])
    with pytest.raises(McpUnavailable):
        _decode([Item("not json")])


def test_the_tool_name_is_the_one_the_workflow_calls():
    import inspect

    from app.graph import kb_search_mcp

    assert TOOL_NAME == "search_knowledge_base"
    assert "search_knowledge_base" in inspect.getsource(kb_search_mcp)


def test_the_fixture_corpus_is_well_formed():
    articles = json.loads(FIXTURE.read_text())
    assert len({article["article_id"] for article in articles}) == len(articles)
    for article in articles:
        assert set(article) >= {"article_id", "title", "category", "excerpt"}
        assert len(article["excerpt"]) > 80
