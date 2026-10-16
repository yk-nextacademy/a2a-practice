"""MCP 서버 연결 관리와 도구 발견.

서버마다 하나의 장기 세션을 유지한다. 세션은 전담 태스크가 열고 닫으며(anyio 규칙),
연결이 끊기면 자동으로 다시 연결한다. 도구 목록은 연결될 때마다 tools/list로 새로 받는다.
"""

import asyncio
import contextlib
import json
import logging

from mcp import Client
from mcp.types import TextContent, TextResourceContents

log = logging.getLogger("mcp_hub")
SEP = "__"  # Claude에 노출하는 도구 이름: <server>__<tool>


class McpConnection:
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self.client: Client | None = None
        self.tools: list = []
        self.server_info = None
        self.last_error: str | None = None
        self._ready = asyncio.Event()
        self._broken = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self.client is not None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            try:
                async with Client(self.url) as client:
                    self.tools = (await client.list_tools()).tools
                    self.server_info = client.server_info
                    self.client, self.last_error = client, None
                    self._broken.clear()
                    self._ready.set()
                    log.info("connected %s (%d tools)", self.name, len(self.tools))
                    await self._broken.wait()
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # 연결 실패는 ExceptionGroup으로 올 수 있다
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            self._ready.clear()
            self.client = None
            await asyncio.sleep(2.0)

    async def get(self, timeout: float = 5.0) -> Client:
        await asyncio.wait_for(self._ready.wait(), timeout)
        return self.client

    async def call(self, fn):
        for attempt in (1, 2):
            client = await self.get()
            try:
                return await fn(client)
            except Exception:
                if self.client is client:
                    self._ready.clear()
                    self._broken.set()
                if attempt == 2:
                    raise

    async def aclose(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


class McpHub:
    def __init__(self, servers: dict[str, str]):
        self.connections = {name: McpConnection(name, url) for name, url in servers.items()}

    def start(self) -> None:
        for conn in self.connections.values():
            conn.start()

    async def aclose(self) -> None:
        for conn in self.connections.values():
            await conn.aclose()

    async def wait_ready(self, timeout: float = 10.0) -> None:
        await asyncio.gather(*(c.get(timeout) for c in self.connections.values()))

    def anthropic_tools(self) -> list[dict]:
        """발견한 MCP 도구를 Claude Messages API의 tools 형식으로 변환한다."""
        tools = []
        for conn in self.connections.values():
            for t in conn.tools:
                tools.append({"name": f"{conn.name}{SEP}{t.name}", "description": t.description or "", "input_schema": t.input_schema})
        return tools

    async def call_tool(self, qualified: str, arguments: dict) -> tuple[bool, str, dict | None]:
        """Returns: (is_error, text, structured)"""
        server, _, tool = qualified.partition(SEP)
        conn = self.connections.get(server)
        if not conn or not tool:
            return True, f"알 수 없는 도구: {qualified}", None
        try:
            result = await conn.call(lambda c: c.call_tool(tool, arguments))
        except asyncio.TimeoutError:
            return True, f"MCP 서버 '{server}'에 연결할 수 없습니다 (연결 대기 시간 초과).", None
        except Exception as exc:
            return True, f"MCP 서버 '{server}' 호출 실패: {type(exc).__name__}: {exc}", None
        text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
        structured = result.structured_content
        if structured is None and not result.is_error:
            with contextlib.suppress(json.JSONDecodeError):
                structured = json.loads(text)
        return bool(result.is_error), text, structured

    async def read_json(self, server: str, uri: str, default=None):
        conn = self.connections.get(server)
        if not conn or not conn.connected:
            return default
        try:
            result = await conn.call(lambda c: c.read_resource(uri))
            content = result.contents[0]
            if isinstance(content, TextResourceContents):
                return json.loads(content.text)
        except Exception as exc:
            log.debug("read_resource failed %s %s: %s", server, uri, exc)
        return default
