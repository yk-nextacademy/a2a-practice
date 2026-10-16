"""Dev/QA 서버 호출과 SSE 중계."""

import asyncio
import json
import time

import httpx

from common import config


class Upstream:
    """요청 단위로 생성한다. 렌더링 중 발생한 상위 서비스 오류를 모아 화면에 표시한다."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.errors: list[str] = []

    async def get(self, base: str, path: str, default=None, **params):
        try:
            r = await self.client.get(base + path, params={k: v for k, v in params.items() if v is not None})
            if r.status_code == 404:
                return default
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            name = "Dev Server" if base == config.DEV_URL else "QA Server"
            msg = f"{name} 응답 없음 ({type(exc).__name__})"
            if msg not in self.errors:
                self.errors.append(msg)
            return default

    async def post(self, base: str, path: str) -> tuple[int, dict]:
        try:
            r = await self.client.post(base + path)
            return r.status_code, (r.json() if r.content else {})
        except httpx.HTTPError as exc:
            return 503, {"error": f"{base} 연결 실패: {type(exc).__name__}"}

    async def health(self, base: str) -> dict:
        started = time.perf_counter()
        try:
            r = await self.client.get(base + "/health", timeout=3)
            r.raise_for_status()
            return {**r.json(), "status": "UP", "latency_ms": int((time.perf_counter() - started) * 1000)}
        except httpx.HTTPError as exc:
            return {"status": "DOWN", "error": type(exc).__name__}


class EventRelay:
    """Dev/QA의 SSE를 구독하여 브라우저 구독자에게 하나의 스트림으로 전달한다."""

    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()
        self.tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self.tasks = [asyncio.create_task(self._follow("Dev", config.DEV_URL)),
                      asyncio.create_task(self._follow("QA", config.QA_URL))]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()

    def publish(self, event: dict) -> None:
        for q in list(self.subscribers):
            q.put_nowait(event)

    async def _follow(self, source: str, base: str) -> None:
        async with httpx.AsyncClient(timeout=None) as client:
            while True:
                try:
                    async with client.stream("GET", base + "/api/events/stream") as r:
                        self.publish({"type": "service.up", "source": source, "payload": {}})
                        async for line in r.aiter_lines():
                            if line.startswith("data: "):
                                event = json.loads(line[6:])
                                event["source"] = source
                                self.publish(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.publish({"type": "service.down", "source": source, "payload": {}})
                await asyncio.sleep(2)

    async def stream(self):
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self.subscribers.discard(q)
