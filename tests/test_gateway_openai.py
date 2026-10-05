"""C10: the OpenAI adapter through the real SDK, and Secret Manager custody.

The adapter is exercised with the pinned `openai` SDK talking to a simulated
server over its own HTTP layer, so these tests prove the SDK's real error
classes, headers, and retry behaviour - not a hand-built imitation of them.
"""

from __future__ import annotations

import base64
import copy
import json
import pickle
import traceback
from typing import Any

import httpx2
import pytest

from app.gateway.contract import CompletionRequest
from app.gateway.credentials import (
    CredentialCache,
    HttpReply,
    SecretManagerSource,
    SecretValue,
    mask,
    validate_new_key,
)
from app.gateway.errors import FailureCode, GatewayFailure
from app.gateway.openai_adapter import OPENAI_REGIONS, OpenAIAdapter

KEY = "sk-proj-client-owned-key-QwErTy-7788"
REQUEST = CompletionRequest(system="Classify.", user="My app crashes.", max_output_tokens=300)


class Server:
    """A simulated OpenAI API. Each test sets `reply`."""

    def __init__(self, reply: httpx2.Response | Exception | None = None):
        self.requests: list[httpx2.Request] = []
        self.reply = reply or chat_reply('{"ok": true}')

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def adapter(self) -> OpenAIAdapter:
        return OpenAIAdapter(httpx2.Client(transport=httpx2.MockTransport(self)))

    def body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def chat_reply(
    content: str, *, finish: str = "stop", usage: dict[str, int] | None = None
) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": usage
            if usage is not None
            else {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
        },
    )


def error_reply(
    status: int, code: str | None, message: str, headers: dict[str, str] | None = None
) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers=headers or {},
        json={
            "error": {"message": message, "type": "invalid_request_error", "code": code},
        },
    )


def complete(server: Server, region: str = "eu", model: str = "gpt-test"):
    return server.adapter().complete(
        SecretValue(KEY), model=model, region=region, request=REQUEST, timeout=5
    )


def failure_of(server: Server, **kwargs: Any) -> GatewayFailure:
    with pytest.raises(GatewayFailure) as raised:
        complete(server, **kwargs)
    return raised.value


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


def test_a_call_goes_to_the_approved_regional_endpoint_with_only_the_client_key(monkeypatch):
    # Everything the SDK would otherwise pick up from the environment.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-developer-environment-key-0000")
    monkeypatch.setenv("OPENAI_ORG_ID", "org-someone-else")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-someone-else")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-environment-key-1111")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.example/v1")
    server = Server()

    result = complete(server)

    (request,) = server.requests
    assert str(request.url) == "https://eu.api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "openai-organization" not in request.headers
    assert "openai-project" not in request.headers
    assert "sk-developer" not in str(request.headers) and "sk-admin" not in str(request.headers)
    body = server.body()
    assert body["model"] == "gpt-test"
    assert body["max_completion_tokens"] == 300
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [
        {"role": "system", "content": "Classify."},
        {"role": "user", "content": "My app crashes."},
    ]
    assert (result.text, result.prompt_tokens, result.completion_tokens, result.finish_reason) == (
        '{"ok": true}',
        42,
        7,
        "stop",
    )


@pytest.mark.parametrize(
    ("region", "host"),
    [
        ("global", "api.openai.com"),
        ("us", "us.api.openai.com"),
        ("eu", "eu.api.openai.com"),
        ("ae", "ae.api.openai.com"),
    ],
)
def test_each_supported_region_has_its_own_endpoint(region, host):
    server = Server()
    complete(server, region=region)
    assert server.requests[0].url.host == host


@pytest.mark.parametrize("region", ["uk", "EU", "", "europe-west2"])
def test_an_unknown_region_is_refused_before_any_request(region):
    server = Server()
    assert failure_of(server, region=region).code is FailureCode.REGION_NOT_SUPPORTED
    assert server.requests == []


def test_the_supported_regions_are_the_sdks_own():
    from openai._data_residency import _DATA_RESIDENCY_BASE_URLS

    assert set(_DATA_RESIDENCY_BASE_URLS) == OPENAI_REGIONS


def test_an_empty_key_is_refused_before_any_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-developer-environment-key-0000")
    server = Server()
    with pytest.raises(GatewayFailure) as raised:
        server.adapter().complete(
            SecretValue(""), model="m", region="eu", request=REQUEST, timeout=5
        )
    assert raised.value.code is FailureCode.CREDENTIAL_MISSING
    assert server.requests == []


def test_missing_usage_is_reported_as_unknown_not_zero():
    server = Server(chat_reply("{}", usage={}))
    server.reply = httpx2.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "length",
                    "message": {"role": "assistant", "content": "{"},
                }
            ],
        },
    )
    result = complete(server)
    assert (result.prompt_tokens, result.completion_tokens, result.finish_reason) == (
        None,
        None,
        "length",
    )


def test_embeddings_request_the_index_width_and_keep_input_order():
    server = Server(
        httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "emb",
                "data": [
                    {"object": "embedding", "index": 1, "embedding": [0.2, 0.2]},
                    {"object": "embedding", "index": 0, "embedding": [0.1, 0.1]},
                ],
                "usage": {"prompt_tokens": 6, "total_tokens": 6},
            },
        )
    )
    result = server.adapter().embed(
        SecretValue(KEY),
        model="emb",
        region="us",
        inputs=["first\nline", "second"],
        dimensions=2,
        timeout=5,
    )
    assert result.vectors == [[0.1, 0.1], [0.2, 0.2]]
    assert result.prompt_tokens == 6
    body = server.body()
    assert body["dimensions"] == 2 and body["input"] == ["first line", "second"]


def test_verification_retrieves_the_model_and_generates_nothing():
    server = Server(
        httpx2.Response(
            200, json={"id": "gpt-test", "object": "model", "created": 1, "owned_by": "openai"}
        )
    )
    server.adapter().verify(SecretValue(KEY), model="gpt-test", region="eu", timeout=5)
    (request,) = server.requests
    assert (request.method, request.url.path) == ("GET", "/v1/models/gpt-test")


# --------------------------------------------------------------------------
# Error translation through the real SDK
# --------------------------------------------------------------------------

LEAKY = f"Incorrect API key provided: {KEY[:8]}...{KEY[-4:]}. You can find your API key at ..."


@pytest.mark.parametrize(
    ("reply", "code", "retry_after"),
    [
        (error_reply(401, "invalid_api_key", LEAKY), FailureCode.CREDENTIAL_INVALID, None),
        (
            error_reply(403, "unsupported_country_region_territory", "Region not supported"),
            FailureCode.PERMISSION_DENIED,
            None,
        ),
        (
            error_reply(404, "model_not_found", "The model does not exist"),
            FailureCode.MODEL_UNAVAILABLE,
            None,
        ),
        (
            error_reply(429, "insufficient_quota", "You exceeded your current quota"),
            FailureCode.QUOTA_EXHAUSTED,
            None,
        ),
        (
            error_reply(429, "rate_limit_exceeded", "Rate limit reached", {"retry-after": "7"}),
            FailureCode.RATE_LIMITED,
            7.0,
        ),
        (
            error_reply(429, None, "Slow down", {"retry-after": "soon"}),
            FailureCode.RATE_LIMITED,
            None,
        ),
        (
            error_reply(400, "context_length_exceeded", "Too long"),
            FailureCode.REQUEST_REJECTED,
            None,
        ),
        (error_reply(422, None, "Unprocessable"), FailureCode.REQUEST_REJECTED, None),
        (error_reply(500, None, "Internal"), FailureCode.PROVIDER_UNAVAILABLE, None),
        (
            error_reply(503, None, "Overloaded", {"retry-after": "2"}),
            FailureCode.PROVIDER_UNAVAILABLE,
            2.0,
        ),
        (error_reply(408, None, "Timeout"), FailureCode.PROVIDER_UNAVAILABLE, None),
    ],
)
def test_provider_errors_become_codes(reply, code, retry_after):
    server = Server(reply)
    failure = failure_of(server)
    assert failure.code is code
    assert failure.status == reply.status_code
    assert failure.retry_after_seconds == retry_after
    assert failure.provider == "openai" and failure.model == "gpt-test"


def test_the_sdk_does_not_retry_behind_the_gateways_back():
    server = Server(error_reply(503, None, "Overloaded"))
    failure_of(server)
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (httpx2.ConnectTimeout("connect timed out"), FailureCode.PROVIDER_TIMEOUT),
        (httpx2.ReadTimeout("read timed out"), FailureCode.PROVIDER_TIMEOUT),
        (httpx2.ConnectError("name resolution failed"), FailureCode.PROVIDER_UNAVAILABLE),
    ],
)
def test_network_failures_become_codes(raised, code):
    assert failure_of(Server(raised)).code is code


def test_a_rejected_key_leaves_no_trace_of_itself_anywhere():
    """OpenAI's 401 message quotes the key's prefix and suffix."""
    failure = failure_of(Server(error_reply(401, "invalid_api_key", LEAKY)))
    logged = "".join(traceback.format_exception(failure))
    for fragment in (KEY, KEY[:8], KEY[-4:], "Incorrect API key"):
        assert fragment not in logged, fragment
        assert fragment not in repr(failure)
    assert str(failure) == "CREDENTIAL_INVALID"
    assert failure.__cause__ is None and failure.__context__ is None


def test_embedding_and_verify_errors_are_translated_too():
    server = Server(error_reply(401, "invalid_api_key", LEAKY))
    with pytest.raises(GatewayFailure) as embedding:
        server.adapter().embed(
            SecretValue(KEY), model="e", region="eu", inputs=["x"], dimensions=2, timeout=5
        )
    with pytest.raises(GatewayFailure) as verification:
        server.adapter().verify(SecretValue(KEY), model="e", region="eu", timeout=5)
    for raised in (embedding.value, verification.value):
        assert raised.code is FailureCode.CREDENTIAL_INVALID
        assert KEY[-4:] not in "".join(traceback.format_exception(raised))


# --------------------------------------------------------------------------
# SecretValue and key shape
# --------------------------------------------------------------------------


def test_a_secret_value_never_prints_itself():
    secret = SecretValue(KEY)
    for rendered in (
        repr(secret),
        str(secret),
        f"{secret}",
        f"{secret!r}",
        "%s" % secret,  # noqa: UP031 - the old formatting path is checked too
        repr([secret]),
        repr({"key": secret}),
    ):
        assert KEY not in rendered
        assert KEY[:-4] not in rendered
    assert secret.hint == "••••7788"
    assert secret.reveal() == KEY


def test_a_secret_value_refuses_to_be_serialised():
    with pytest.raises(TypeError):
        pickle.dumps(SecretValue(KEY))
    with pytest.raises(TypeError):
        copy.deepcopy(SecretValue(KEY))
    with pytest.raises(TypeError):
        json.dumps({"key": SecretValue(KEY)})


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "not set"), ("", "not set"), ("short-key", "not set"), (KEY, "••••7788")],
)
def test_masking_shows_at_most_the_last_four(value, expected):
    assert mask(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "short",
        "sk-" + "a" * 600,
        "sk-has space-inside-000000",
        "sk-new\nline-00000000000",
        "sk-tab\tchar-000000000000",
        "sk-\x00nul-000000000000000",
    ],
)
def test_malformed_keys_are_refused_before_storage(value):
    with pytest.raises(GatewayFailure) as raised:
        validate_new_key(value)
    assert raised.value.code is FailureCode.CREDENTIAL_INVALID


def test_surrounding_whitespace_is_trimmed_from_a_pasted_key():
    assert validate_new_key(f"  {KEY}\n") == KEY


# --------------------------------------------------------------------------
# Secret Manager over REST
# --------------------------------------------------------------------------

SECRET = "projects/client-prod/secrets/resolveflow-prod-llm-provider-api-key"


class FakeHttp:
    def __init__(self, *replies: HttpReply):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> HttpReply:
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json})
        return self.replies.pop(0)


def token() -> HttpReply:
    return HttpReply(200, {"access_token": "ya29.runtime", "expires_in": 3599})


def payload(value: str) -> HttpReply:
    return HttpReply(
        200,
        {
            "name": f"{SECRET}/versions/3",
            "payload": {"data": base64.b64encode(value.encode()).decode()},
        },
    )


def test_the_latest_version_is_read_with_the_runtime_identity():
    http = FakeHttp(token(), payload(KEY + "\n"))
    value = SecretManagerSource(http).access(SECRET)
    assert value.reveal() == KEY
    metadata, access = http.requests
    assert metadata["url"].startswith("http://metadata.google.internal/")
    assert metadata["headers"] == {"Metadata-Flavor": "Google"}
    assert (
        access["url"] == f"https://secretmanager.googleapis.com/v1/{SECRET}/versions/latest:access"
    )
    assert access["headers"] == {"Authorization": "Bearer ya29.runtime"}


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (
            HttpReply(
                400,
                {
                    "error": {
                        "status": "FAILED_PRECONDITION",
                        "message": "Secret version is in DISABLED state.",
                    }
                },
            ),
            FailureCode.CREDENTIAL_EXPIRED,
        ),
        (HttpReply(404, {"error": {"status": "NOT_FOUND"}}), FailureCode.CREDENTIAL_MISSING),
        (
            HttpReply(403, {"error": {"status": "PERMISSION_DENIED"}}),
            FailureCode.CREDENTIAL_ACCESS_DENIED,
        ),
        (
            HttpReply(400, {"error": {"status": "INVALID_ARGUMENT"}}),
            FailureCode.CREDENTIAL_ACCESS_DENIED,
        ),
        (HttpReply(500, {}), FailureCode.CREDENTIAL_ACCESS_DENIED),
        (HttpReply(200, {"payload": {}}), FailureCode.CREDENTIAL_MISSING),
        (HttpReply(200, {"payload": {"data": "!!not base64!!"}}), FailureCode.CREDENTIAL_MISSING),
        (payload("   "), FailureCode.CREDENTIAL_MISSING),
    ],
)
def test_secret_manager_outcomes_become_codes(reply, code):
    with pytest.raises(GatewayFailure) as raised:
        SecretManagerSource(FakeHttp(token(), reply)).access(SECRET)
    assert raised.value.code is code


@pytest.mark.parametrize(
    "name",
    [
        "",
        KEY,
        "projects/x/secrets/y/versions/1",
        "projects/X/secrets/y",
        "https://secretmanager.googleapis.com/v1/projects/x/secrets/y",
    ],
)
def test_anything_but_a_secret_name_is_refused_without_a_request(name):
    http = FakeHttp()
    with pytest.raises(GatewayFailure):
        SecretManagerSource(http).access(name)
    assert http.requests == []


def test_no_runtime_identity_means_no_access():
    with pytest.raises(GatewayFailure) as raised:
        SecretManagerSource(FakeHttp(HttpReply(404, {}))).access(SECRET)
    assert raised.value.code is FailureCode.CREDENTIAL_ACCESS_DENIED


def test_a_new_version_is_added_encoded_and_nothing_else_is_sent():
    http = FakeHttp(token(), HttpReply(200, {"name": f"{SECRET}/versions/4"}))
    version = SecretManagerSource(http).add_version(SECRET, SecretValue(KEY))
    assert version == f"{SECRET}/versions/4"
    request = http.requests[1]
    assert request["method"] == "POST"
    assert request["url"] == f"https://secretmanager.googleapis.com/v1/{SECRET}:addVersion"
    assert request["json"] == {"payload": {"data": base64.b64encode(KEY.encode()).decode()}}


def test_a_refused_version_write_is_a_code():
    http = FakeHttp(token(), HttpReply(403, {"error": {"status": "PERMISSION_DENIED"}}))
    with pytest.raises(GatewayFailure) as raised:
        SecretManagerSource(http).add_version(SECRET, SecretValue(KEY))
    assert raised.value.code is FailureCode.CREDENTIAL_ACCESS_DENIED


def test_the_cache_expires_and_can_be_emptied():
    clock = {"t": 0.0}
    reads: list[str] = []

    class Source:
        def access(self, name: str) -> SecretValue:
            reads.append(name)
            return SecretValue(f"{KEY}-{len(reads)}")

        def add_version(self, name: str, value: SecretValue) -> str:
            raise AssertionError

    cache = CredentialCache(Source(), ttl_seconds=300, clock=lambda: clock["t"])
    first = cache.get(SECRET)
    clock["t"] = 299
    assert cache.get(SECRET) == first
    clock["t"] = 300
    second = cache.get(SECRET)
    assert second != first
    cache.invalidate(SECRET)
    assert cache.get(SECRET) != second
    assert len(reads) == 3


def test_no_gateway_code_raises_a_new_exception_inside_an_except_block():
    """Raising inside `except` chains the caught error as __context__.

    Here the caught error may be an SDK error quoting the key, an httpx error
    holding the request and its bearer token, or a UnicodeDecodeError holding
    the raw secret bytes. Only a bare re-raise is allowed inside a handler.
    """
    import ast
    from pathlib import Path

    offenders = []
    for path in sorted(Path("app/gateway").glob("*.py")):
        tree = ast.parse(path.read_text())
        for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
            for node in ast.walk(handler):
                if isinstance(node, ast.Raise) and node.exc is not None:
                    offenders.append(f"{path}:{node.lineno}")
    assert offenders == []


def test_an_undecodable_secret_leaves_no_trace_of_its_bytes():
    raw = base64.b64encode(b"sk-proj-\xff\xfe-rest-of-the-key-0000").decode()
    with pytest.raises(GatewayFailure) as raised:
        SecretManagerSource(FakeHttp(token(), HttpReply(200, {"payload": {"data": raw}}))).access(
            SECRET
        )
    assert raised.value.code is FailureCode.CREDENTIAL_MISSING
    assert raised.value.__context__ is None and raised.value.__cause__ is None
    assert "rest-of-the-key" not in "".join(traceback.format_exception(raised.value))


def test_a_network_failure_reaching_secret_manager_carries_no_request(monkeypatch):
    import httpx

    from app.gateway.credentials import HttpxTransport

    def refuse(*args, **kwargs):
        raise httpx.ConnectError(
            "unreachable",
            request=httpx.Request(
                "GET", "https://x", headers={"Authorization": "Bearer ya29.secret-token"}
            ),
        )

    monkeypatch.setattr(httpx, "request", refuse)
    with pytest.raises(GatewayFailure) as raised:
        HttpxTransport().request(
            "GET", "https://x", headers={"Authorization": "Bearer ya29.secret-token"}, timeout=1
        )
    assert raised.value.code is FailureCode.CREDENTIAL_ACCESS_DENIED
    assert raised.value.__context__ is None and raised.value.__cause__ is None


def test_the_client_itself_holds_no_organization_or_project_from_the_environment(monkeypatch):
    # Headers are also omitted, but the client's own attributes are what the
    # SDK copies into derived clients (`with_options`), so both layers matter.
    monkeypatch.setenv("OPENAI_ORG_ID", "org-someone-else")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-someone-else")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-environment-key-1111")
    client = Server().adapter()._client(SecretValue(KEY), "eu", 5)
    assert (client.organization, client.project, client.admin_api_key) == ("", "", "")
    derived = client.with_options(timeout=1)
    assert (derived.organization, derived.project) == ("", "")
