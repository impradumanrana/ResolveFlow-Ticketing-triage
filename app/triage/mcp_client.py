"""A persistent MCP client for the knowledge server.

The MVP started a server process, asked one question, and stopped it. That is
fine for a demo and wrong for a worker: every conversation would pay process
start-up before it could retrieve anything. This client starts the server once
and keeps the session open.

Failure handling is the point of the design. A knowledge search that hangs must
not hang the pipeline, so every call has a deadline; and a session that has
failed must not be reused, so a failure closes it and the next call starts a
fresh process. Either way the caller sees `McpUnavailable`, which the workflow
turns into `MCP_UNAVAILABLE` and a human route.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from app.triage.knowledge_mcp import RESULT_FIELDS, TOOL_NAME

DEFAULT_TIMEOUT_SECONDS = 20.0
STARTUP_TIMEOUT_SECONDS = 30.0
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class McpUnavailable(RuntimeError):
    """The knowledge boundary could not answer. Auto-resolution is off."""


class KnowledgeMcpClient:
    """One knowledge server process, reused across conversations."""

    def __init__(
        self,
        *,
        env: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        module: str = "app.triage.knowledge_mcp",
    ):
        self.timeout = timeout
        self.module = module
        self.env = {
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT),
            "PYTHONUNBUFFERED": "1",
            **(env or {}),
        }
        self.connected = False
        self.last_request: dict[str, Any] | None = None
        self.last_response: Any = None
        self.last_duration_ms = 0.0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    # -- lifecycle ------------------------------------------------------

    def _start_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        ready = threading.Event()
        loop = asyncio.new_event_loop()

        def run() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()

        self._thread = threading.Thread(target=run, name="mcp-knowledge", daemon=True)
        self._thread.start()
        ready.wait(STARTUP_TIMEOUT_SECONDS)
        self._loop = loop
        return loop

    def _submit(self, coroutine: Any, timeout: float) -> Any:
        loop = self._start_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        timed_out = False
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            timed_out = True
        if timed_out:
            raise McpUnavailable("Knowledge search timed out.")

    async def _open(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", self.module], env=self.env
        )
        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(parameters))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack, self._session = stack, session

    async def _close(self) -> None:
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:  # pragma: no cover - the process is going away
                pass

    def close(self) -> None:
        with self._lock:
            if self._loop is None:
                return
            try:
                self._submit(self._close(), timeout=10)
            except Exception:  # pragma: no cover - shutdown is best effort
                pass
            self.connected = False
            loop, self._loop = self._loop, None
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> KnowledgeMcpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- the tool call --------------------------------------------------

    async def _call(self, query: str, category: str, top_k: int) -> list[dict[str, Any]]:
        if self._session is None:
            await self._open()
        assert self._session is not None
        response = await self._session.call_tool(
            TOOL_NAME, {"query": query, "category": category, "top_k": top_k}
        )
        if getattr(response, "isError", False):
            detail = " ".join(str(getattr(item, "text", "")) for item in response.content)
            raise McpUnavailable(f"Knowledge search failed: {detail[:300]}")
        return _decode(response.content)

    def search(self, query: str, category: str = "", top_k: int = 3) -> list[dict[str, Any]]:
        started = time.perf_counter()
        self.last_request = {"query": query, "category": category, "top_k": top_k}
        with self._lock:
            try:
                results = self._submit(self._call(query, category, top_k), self.timeout)
            except Exception as error:
                # A failed session is never reused: the next call gets a new
                # process rather than a broken pipe.
                try:
                    self._submit(self._close(), timeout=5)
                except Exception:  # pragma: no cover - already failing
                    pass
                self.connected = False
                self.last_duration_ms = round((time.perf_counter() - started) * 1000, 2)
                self.last_response = {"error": type(error).__name__}
                raise McpUnavailable(type(error).__name__) from None
            self.connected = True
            self.last_response = results
            self.last_duration_ms = round((time.perf_counter() - started) * 1000, 2)
            return results


def _decode(content: Any) -> list[dict[str, Any]]:
    """Parse the tool payload and refuse a shape the workflow cannot cite."""
    results: list[dict[str, Any]] = []
    for item in content or []:
        text = getattr(item, "text", None)
        if text is None:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise McpUnavailable("Knowledge search returned malformed JSON.") from error
        if isinstance(parsed, list):
            results.extend(parsed)
        elif isinstance(parsed, dict):
            results.append(parsed)

    for result in results:
        if not isinstance(result, dict) or not set(RESULT_FIELDS).issubset(result):
            raise McpUnavailable("Knowledge search returned an invalid result shape.")
    return results
