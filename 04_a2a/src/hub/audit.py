"""A2A 트래픽 감사 로그.

httpx 전송 계층을 감싸서 Hub가 보내고 받는 모든 원문(JSON-RPC 요청, JSON/SSE 응답)을 기록한다.
스트리밍 응답은 흘려보내면서 복사하므로 실시간성이 깨지지 않는다.
"""

import contextvars
import json
import re
import time

import httpx

from common.db import Database, now_ms

# 현재 처리 중인 사이클 (워크플로 태스크마다 설정)
current_cycle: contextvars.ContextVar[int | None] = contextvars.ContextVar("current_cycle", default=None)

TASK_ID = re.compile(r'"(?:taskId|id)"\s*:\s*"([0-9a-f-]{36})"')
LIMIT = 60_000


class _TeeStream(httpx.AsyncByteStream):
    def __init__(self, inner, on_close):
        self._inner = inner
        self._chunks: list[bytes] = []
        self._on_close = on_close

    async def __aiter__(self):
        async for chunk in self._inner:
            self._chunks.append(chunk)
            yield chunk

    async def aclose(self):
        await self._inner.aclose()
        self._on_close(b"".join(self._chunks).decode("utf-8", "replace"))


class AuditTransport(httpx.AsyncBaseTransport):
    def __init__(self, db: Database, agent_names: dict[str, str]):
        self._inner = httpx.AsyncHTTPTransport()
        self.db = db
        self.agent_names = agent_names  # base url -> agent name (레지스트리가 채운다)

    def _agent(self, url: str) -> str | None:
        return next((name for base, name in self.agent_names.items() if url.startswith(base)), None)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8", "replace") if request.content else ""
        method, task_id, streaming = request.method, None, False
        if body:
            try:
                rpc = json.loads(body)
                method = rpc.get("method", method)
                streaming = method in ("SendStreamingMessage", "SubscribeToTask")
                params = rpc.get("params", {})
                task_id = (params.get("message") or {}).get("taskId") or params.get("id") or None
                body = json.dumps(rpc, ensure_ascii=False, indent=1)
            except (ValueError, AttributeError):
                pass
        elif request.url.path.endswith("agent-card.json"):
            method = "GetAgentCard"
        url = str(request.url)
        row_id = self.db.execute(
            "INSERT INTO a2a_messages (ts, cycle_id, agent, method, url, task_id, request_body, streaming) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            now_ms(), current_cycle.get(), self._agent(url), method, url, task_id, body[:LIMIT], int(streaming),
        )
        started = time.perf_counter()
        try:
            response = await self._inner.handle_async_request(request)
        except httpx.HTTPError as exc:
            self.db.execute("UPDATE a2a_messages SET response_body = ?, duration_ms = ? WHERE id = ?",
                            f"{type(exc).__name__}: {exc}", int((time.perf_counter() - started) * 1000), row_id)
            raise

        def finish(text: str) -> None:
            found = TASK_ID.search(text)
            self.db.execute(
                "UPDATE a2a_messages SET status = ?, duration_ms = ?, response_body = ?, task_id = COALESCE(task_id, ?) WHERE id = ?",
                response.status_code, int((time.perf_counter() - started) * 1000), _pretty(text)[:LIMIT],
                found.group(1) if found else None, row_id,
            )

        return httpx.Response(response.status_code, headers=response.headers,
                              stream=_TeeStream(response.stream, finish), extensions=response.extensions)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _pretty(text: str) -> str:
    """JSON은 들여쓰기, SSE는 data 라인별로 들여쓰기한다."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.dumps(json.loads(stripped), ensure_ascii=False, indent=1)
        except ValueError:
            return text
    lines = []
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                lines.append("data: " + json.dumps(json.loads(line[5:].strip()), ensure_ascii=False))
                continue
            except ValueError:
                pass
        if line.strip():
            lines.append(line)
    return "\n".join(lines)
