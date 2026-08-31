from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClient:
    def __init__(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        self.server_params = StdioServerParameters(
            command=sys.executable,
            args=[str(project_root / "app" / "mcp_server.py")],
            env={**os.environ, "PYTHONPATH": str(project_root)},
        )
        self.connected = False
        self.last_request: dict | None = None
        self.last_response: list[dict] | dict | None = None
        self.last_duration_ms = 0.0

    async def _search_async(self, query: str, category: str, top_k: int = 3) -> list[dict]:
        start = time.perf_counter()
        self.last_request = {"query": query, "category": category, "top_k": top_k}
        try:
            async with stdio_client(self.server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.call_tool(
                        "search_knowledge_base",
                        {"query": query, "category": category, "top_k": top_k},
                    )
                    payload = response.content
                    data: list[dict] = []
                    for item in payload:
                        text = getattr(item, "text", None)
                        if text is None:
                            continue
                        try:
                            parsed = json.loads(text)
                        except json.JSONDecodeError:
                            data.append({"raw": text})
                            continue
                        if isinstance(parsed, list):
                            data.extend(parsed)
                        elif isinstance(parsed, dict):
                            data.append(parsed)
                    self.connected = True
                    self.last_response = data
                    self.last_duration_ms = round((time.perf_counter() - start) * 1000, 2)
                    return data
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self.connected = False
            self.last_response = {"error": str(exc)}
            self.last_duration_ms = round((time.perf_counter() - start) * 1000, 2)
            raise

    def search(self, query: str, category: str, top_k: int = 3) -> list[dict]:
        return asyncio.run(self._search_async(query, category, top_k))
